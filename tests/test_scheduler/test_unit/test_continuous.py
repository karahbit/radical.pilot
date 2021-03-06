# pylint: disable=protected-access, unused-argument
# pylint: disable=no-value-for-parameter
__copyright__ = "Copyright 2013-2016, http://radical.rutgers.edu"
__license__ = "MIT"

from unittest import TestCase
import glob
import pytest
import radical.utils as ru
import radical.pilot.constants as rpc
from   radical.pilot.agent.scheduler.continuous import Continuous

try:
    import mock
except ImportError:
    from unittest import mock


# ------------------------------------------------------------------------------
#
class TestContinuous(TestCase):

    # ------------------------------------------------------------------------------
    #
    def setUp(self):

        ret = list()
        for fin in glob.glob('tests/test_scheduler/test_unit/test_cases_continuous/unit*.json'):
            test_cases = ru.read_json(fin)
            ret.append(test_cases)
        cfg_tests = ru.read_json('tests/test_scheduler/test_unit/test_cases_continuous/test_continuous.json')
        return cfg_tests, ret

    # --------------------------------------------------------------------------
    #
    def tearDown(self):
        pass

    # --------------------------------------------------------------------------
    #
    @mock.patch.object(Continuous, '__init__', return_value=None)
    def test_configure(self, mocked_init):

        cfg, _ = self.setUp()
        component = Continuous(cfg=None, session=None)	
        component._cfg =  mock.Mock()	
        component._log = ru.Logger('dummy')
        component._rm_node_list = [["a", 1],	
                                   ["b", 2],["c",3]]	
        component._rm_cores_per_node = 8	
        component._rm_gpus_per_node  = 2	
        component._rm_lfs_per_node   = {"path": "/dev/null", "size": 0}	
        component._rm_mem_per_node   = 128	
        component.nodes = [{'name'  : 'a',
                          'uid'   : 2,
                          'cores' : [1, 2, 3, 4, 6, 0, 9, 8],
                          'lfs'   : {"size": 1234,
                                     "path" : "/dev/null"},
                          'mem'   : 1024,
                          'gpus'  : [1, 2]}]
        try:     
            for i in range (len(cfg['cfg']['rm_info'])):
                rm_info = cfg['cfg']['rm_info'][i]
                component._configure()
                self.assertEqual(component.nodes[0]['cores'], [rpc.FREE] * rm_info['cores_per_node'])	
                self.assertEqual(component.nodes[0]['gpus'],  [rpc.FREE] * rm_info['gpus_per_node'])
        except:	
            with pytest.raises(AssertionError):	
                component._configure()	
                raise


    # --------------------------------------------------------------------------
    #
    @mock.patch.object(Continuous, '__init__', return_value=None)
    @mock.patch.object(Continuous, '_configure', return_value=None)
    def test_find_resources(self,
                            mocked_init,
                            mocked_configure):

        _, cfg = self.setUp()
        component = Continuous(cfg=None, session=None)
        component.node = {'name'  : 'a',
                          'uid'   : 2,
                          'cores' : [0, 0, 0, 0, 0, 0, 0, 0,
                                     0, 0, 0, 0, 0, 0, 0, 0],
                          'lfs'   : {"size": 1234,
                                     "path" : "/dev/null"},
                          'mem'   : 1024,
                          'gpus'  : [0, 0]}
        component._log = ru.Logger('dummy')
        component._rm_lfs_per_node = {"path" : "/dev/null", "size" : 1234}
        component.cores_per_slot   = 16
        component.gpus_per_slot    = 2
        component.lfs_per_slot     = 1234
        component.mem_per_slot     = 1024
        component.find_slot        = 1

        try:
            test_slot = component._find_resources(
                node=component.node,
                find_slots=component.find_slot,
                cores_per_slot=component.cores_per_slot,
                gpus_per_slot=component.gpus_per_slot,
                lfs_per_slot=component.lfs_per_slot,
                mem_per_slot=component.mem_per_slot,
                partial='None')
            self.assertEqual([cfg[1]['setup']['lm']['slots']], test_slot)
        except:
            with pytest.raises(AssertionError):
                raise


    # --------------------------------------------------------------------------
    #
    @mock.patch.object(Continuous, '__init__', return_value=None)
    @mock.patch.object(Continuous, '_configure', return_value=None)
    @mock.patch.object(Continuous, '_find_resources',
                       return_value=[{'name'    : 'a',
                                      'uid'     : 1,
                                      'core_map': [[0]],
                                      'gpu_map' : [[0]],
                                      'lfs'     : {'path': '/dev/null',
                                                   'size': 1234},
                                      'mem'     : 128}])
    def test_schedule_unit(self,
                           mocked_init,
                           mocked_configure,
                           mocked_find_resources):

        _, cfg = self.setUp()
        component = Continuous(cfg=None, session=None)
        unit = dict()
        unit['uid'] = cfg[1]['unit']['uid'] 
        unit['description'] = cfg[1]['unit']['description']      
        component.nodes = cfg[1]['setup']['lm']['slots']['nodes']

        component._rm_cores_per_node = 32
        component._rm_gpus_per_node  = 2
        component._rm_lfs_per_node   = {"size": 0, "path": "/dev/null"}
        component._rm_mem_per_node   = 1024
        component._rm_lm_info = 'INFO'
        component._log = ru.Logger('dummy')
        component._node_offset = 0 
        test_slot =  {'cores_per_node': 32,
                      'gpus_per_node': 2,
                      'lfs_per_node': {'path': '/dev/null', 'size': 0},
                      'lm_info': 'INFO',
                      'mem_per_node': 1024,
                      'nodes': [{'core_map': [[0]],
                                 'gpu_map' : [[0]],
                                 'lfs': {'path': '/dev/null', 'size': 1234},
                                 'mem': 128,
                                 'name': 'a',
                                 'uid': 1}]}
        try:
            self.assertEqual(component.schedule_unit(unit), test_slot)
        except:
            with pytest.raises(AssertionError):
                raise


    # --------------------------------------------------------------------------
    #
    @mock.patch.object(Continuous, '__init__', return_value=None)
    def test_unschedule_unit(self, mocked_init):

        component = Continuous(cfg=None, session=None)
        _, cfg = self.setUp()
        unit = dict()
        unit['description'] = cfg[1]['unit']['description']
        unit['slots'] = cfg[1]['setup']['lm']['slots']
        component.nodes = cfg[1]['setup']['lm']['slots']['nodes']
        component._log = ru.Logger('dummy')
        component.unschedule_unit(unit)
        try:
            self.assertEqual(component.nodes[0]['cores'], [0])
            self.assertEqual(component.nodes[0]['gpus'], [0])
        except:
            with pytest.raises(AssertionError):
                raise        
