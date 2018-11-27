
# pylint: disable=protected-access, unused-argument

import os
import glob

import radical.utils as ru
from   radical.pilot.agent.lm.orte import ORTE


try:
    import mock
except ImportError:
    from unittest import mock


# ------------------------------------------------------------------------------
# 
def setUp():

    pwd = os.path.dirname(__file__)
    ret = list()
    for fin in glob.glob('%s/test_cases/unit.*.json' % pwd):
        tc = ru.read_json(fin)
        ret.append([tc['unit'],  tc['results']])

    return ret


# ------------------------------------------------------------------------------
#
def tearDown():

    pass


# ------------------------------------------------------------------------------
#
@mock.patch.object(ORTE, '__init__',   return_value=None)
@mock.patch.object(ORTE, '_configure', return_value=None)
@mock.patch('radical.utils.raise_on')
def test_construct_command(mocked_init, 
                           mocked_configure,
                           mocked_raise_on):

    test_cases = setUp()

    component = ORTE(name=None, cfg=None, session=None)
    component.name = 'orte'
    component._log = ru.get_logger('dummy')
    component.launch_command = 'orterun'

    for unit, results in test_cases:

        command, _ = component.construct_command(unit, None)
        result     = results['lm.orte']
        print command
        print result

        assert(command == result)

    tearDown()


# ------------------------------------------------------------------------------
