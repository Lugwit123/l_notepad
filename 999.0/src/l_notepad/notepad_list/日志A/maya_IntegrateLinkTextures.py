import sys
sys.path.append(r"D:\TD_Depot\Software\Lugwit_syncPlug\lugwit_insapp\trayapp\rez-package-source\pytracemp\999.0\src")
import pytracemp
pytracemp = pytracemp.reload_pytracemp()
lprint = pytracemp.LPrint()
from imp import reload
from pyblish_plugins_global.integrate import link_textures
reload(link_textures)
IntegrateLinkTextures=link_textures.IntegrateLinkTextures
class Ctx(object):
    data = {
        'publish_config': {
            'default_loc_prefix': r'J:\Projects',
            'vendor_prefix': r'J:\Vendor\Projects',
        },
        'session_config': {'is_wp': False},
    }

class Inst(object):
    def __init__(self):
        self.data = {
            'link_textures': ['J:/Projects/h74/asset_library/env/td_test_env_a/mod/v029/zhuchangshow_body_shd_nor_1001.tx',],
            'component_links': {
                8693760: {
                    'path': 'J:/Projects/h74/asset_library/env/td_test_env_a/mod/v029/zhuchangshow_body_shd_nor_1002.tx',
                    'id': 8693760,
                    'version_name': 'env_td_test_env_a_mod.png V29',
                },
            },
            'publishDir': r'J:\Projects\h74\asset_library\env\td_test_env_a\mod\v031',
        }
        self.context = Ctx()

lprint.trace_log_enable = True
lprint.trace_start(trace_depth=4,trace_use_profile=False, trace_log_stem="integrate_link_textures") 
IntegrateLinkTextures().process(Inst())
lprint.trace_stop()