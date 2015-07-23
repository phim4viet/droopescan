from __future__ import print_function
from cement.core import controller
from collections import OrderedDict
from common.functions import template
from common import template
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from plugins.internal.base_plugin import BasePlugin
from plugins.internal.base_plugin_internal import BasePluginInternal
import common
import common.functions as f
import common.plugins_util as pu
import common.versions as v
import traceback

try:
    from urlparse import urlparse
except ImportError:
    from urllib.parse import urlparse

class Scan(BasePlugin):

    IDENTIFY_BATCH_SIZE = 30

    class Meta:
        label = 'scan'
        description = 'cms scanning functionality.'
        stacked_on = 'base'
        stacked_type = 'nested'

        epilog = "\n"

        argument_formatter = common.SmartFormatter
        epilog = template("help_epilog.mustache")

        arguments = [
                (['-u', '--url'], dict(action='store', help='')),
                (['-U', '--url-file'], dict(action='store', help='''A file which
                    contains a list of URLs.''')),

                (['--enumerate', '-e'], dict(action='store', help='R|' +
                    common.template('help_enumerate.mustache'),
                    choices=common.enum_list(common.Enumerate), default='a')),
                (['--method'], dict(action='store', help='R|' +
                    common.template('help_method.mustache'), choices=common.enum_list(common.ScanningMethod))),
                (['--verb'], dict(action='store', help="""The HTTP verb to use;
                    the default option is head, except for version enumeration
                    requests, which are always get because we need to get the hash
                    from the file's contents""", default='head',
                    choices=common.enum_list(common.Verb))),
                (['--threads', '-t'], dict(action='store', help='''Number of
                    threads. Default 4.''', default=4, type=int)),
                (['--number', '-n'], dict(action='store', help='''Number of
                    words to attempt from the plugin/theme dictionary. Default
                    is 1000. Use -n 'all' to use all available.''',
                    default=BasePluginInternal.NUMBER_DEFAULT)),
                (['--plugins-base-url'], dict(action='store', help="""Location
                    where the plugins are stored by the CMS. Default is the CMS'
                    default location. First %%s in string will be replaced with
                    the url, and the second one will be replaced with the module
                    name. E.g. '%%ssites/all/modules/%%s/'""")),
                (['--themes-base-url'], dict(action='store', help='''Same as
                    above, but for themes.''')),
                (['--timeout'], dict(action='store', help="""How long to wait
                    for an HTTP response before timing out (in seconds).""",
                    default=45, type=int)),
                (['--timeout-host'], dict(action='store', help="""Maximum time
                    to spend per host (in seconds).""", default=1800, type=int)),
                (['--no-follow-redirects'], dict(action='store_false', help="""Prevent
                    the following of redirects.""", dest="follow_redirects", default=True)),
                (['--host'], dict(action='store', help="""Override host header
                    with this value.""", default=None)),

                (['--output', '-o'], dict(action='store', help='Output format',
                    choices=common.enum_list(common.ValidOutputs), default='standard')),
                (['--debug-requests'], dict(action='store_true', help="""Prints every
                    HTTP request made and the response returned from the server
                    for debugging purposes. Disables threading and loading
                    bars.""", default=False)),
                (['--error-log'], dict(action='store', help='''A file to store the
                    errors on.''', default=None)),
            ]

    @controller.expose(hide=True)
    def default(self):
        plugins = pu.plugins_base_get()
        opts = self._options(self.app.pargs)
        self._general_init(opts)
        instances = self._instances_get(opts, plugins)

        follow_redirects = opts['follow_redirects']
        opts['follow_redirects'] = False

        if 'url_file' in opts:
            self._process_scan_url_file(opts, instances, follow_redirects)
        else:
            cms_name, url = self._process_cms_identify(opts['url'], opts, instances,
                    follow_redirects)

            inst_dict = instances[cms_name]
            inst = inst_dict['inst']
            opts_clone = dict(opts)
            opts_clone['url'] = url

            inst.process_url(opts_clone, **inst_dict['kwargs'])

    def _process_scan_url_file(self, opts, instances, follow_redirects):
        futures = []
        with open(opts['url_file']) as url_file:
            with ThreadPoolExecutor(max_workers=opts['threads']) as executor:
                i = 0
                for url in url_file:
                    url = url.strip()
                    future = executor.submit(self._process_cms_identify, url,
                            opts, instances, follow_redirects)

                    futures.append({
                        'url': url,
                        'future': future
                    })

                    if i % self.IDENTIFY_BATCH_SIZE == 0 and i != 0:
                        self._process_identify_futures(futures, opts, instances)
                        futures = []

                    i += 1

                if len(futures) > 0:
                    self._process_identify_futures(futures, opts, instances)

    def _process_cms_identify(self, url, opts, instances, follow_redirects):
        contains_host = self._line_contains_host(url)
        if contains_host:
            url, new_opts = self._process_multiline_host(url, opts)
            host_header = new_opts['headers']['Host']
        else:
            new_opts = opts

        url = f.repair_url(url, self.out)

        if follow_redirects:
            print("lalal")
            redir_url = self.determine_redirect(url, new_opts['verb'], new_opts['timeout'],
                    new_opts['headers'])

            if contains_host:
                parsed = urlparse(redir_url)
                if parsed.netloc != host_header:
                    # DNS is necessary in this case.
                    new_opts = opts
                    url = redir_url
                else:
                    orig_parsed = urlparse(url)
                    parsed = parsed._replace(netloc=orig_parsed.netloc)
                    url = parsed.geturl()
            else:
                url = redir_url

        found = False
        for cms_name in instances:
            inst_dict = instances[cms_name]
            inst = inst_dict['inst']
            vf = inst_dict['vf']

            if inst.cms_identify(vf, url, new_opts['timeout'], new_opts['headers']) == True:
                found = True
                break

        if not found:
            return None, None
        else:
            return cms_name, (url, new_opts)

    def _process_identify_futures(self, futures, opts, instances):
        to_scan = {}
        for future_dict in futures:
            future = future_dict['future']

            try:
                cms_name, result_tuple = future.result(timeout=opts['timeout_host'])

                if cms_name != None:
                    if cms_name not in to_scan:
                        to_scan[cms_name] = []

                    to_scan[cms_name].append(result_tuple)
            except:
                exc = traceback.format_exc()
                self.out.warn(("Line '%s' raised:\n" % future_dict['url']) + exc,
                        whitespace_strp=False)

        if to_scan:
            self._process_scan(opts, instances, to_scan)

    def _process_scan(self, opts, instances, to_scan):
        for cms_name in to_scan:
            inst_dict = instances[cms_name]
            cms_urls = to_scan[cms_name]
            inst = inst_dict['inst']
            kwargs = dict(inst_dict['kwargs'])
            del kwargs['hide_progressbar']

            if len(cms_urls) > 0:
                inst.process_url_iterable(cms_urls, opts, **kwargs)

    def _instances_get(self, opts, plugins):
        instances = OrderedDict()
        preferred_order = ['wordpress', 'joomla', 'drupal']

        for cms_name in preferred_order:
            for plugin in plugins:
                plugin_name = plugin.__name__.lower()

                if cms_name == plugin_name:
                    instances[plugin_name] = self._instance_get(plugin, opts)

        for plugin in plugins:
            plugin_name = plugin.__name__.lower()
            if plugin_name not in preferred_order:
                instances[plugin_name] = self._instance_get(plugin, opts)

        return instances

    def _instance_get(self, plugin, opts):
        inst = plugin()
        hp, func, enabled_func = inst._general_init(opts)
        name = inst._meta.label
        vf = v.VersionsFile(inst.versions_file)

        return {
            'inst': inst,
            'vf': vf,
            'kwargs': {
                'hide_progressbar': hp,
                'functionality': func,
                'enabled_functionality': enabled_func
            }
        }

