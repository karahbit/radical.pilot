
__copyright__ = "Copyright 2013-2016, http://radical.rutgers.edu"
__license__   = "MIT"

import copy
import pprint
import threading as mt

from .continuous import Continuous

from ... import states    as rps
from ... import compute_unit_description as rpcud


# ------------------------------------------------------------------------------
#
# This is a simple extension of the Continuous scheduler which evaluates the
# `colocate` tag of arriving units, which is expected to have the form
#
#   colocate : {'ns'   : <string>,
#               'size' : <int>}
#
# where 'ns' (for namespace) is a bag ID, and 'size' is the number of tasks in
# that bag of tasks that need to land on the same host.  The semantics of the
# scheduler is that, for any given namespace, it will schedule either all tasks
# in that ns at the same time on the same node, or will schedule no task of that
# ns at all.
#
# The dominant use case for this scheduler is the execution of coupled
# applications which exchange data via shared local files or shared memory.
#
# FIXME: - failed tasks cannot yet considered, subsequent tasks in the same ns
#          will be scheduled anyway.
#
class ContinuousColo(Continuous):

    # --------------------------------------------------------------------------
    #
    def __init__(self, cfg, session):

        Continuous.__init__(self, cfg, session)


    # --------------------------------------------------------------------------
    #
    def _configure(self):

        Continuous._configure(self)

        # a 'bag' entry will look like this:
        #
        #   {
        #      'size': 128,    # number of units to expect
        #      'uids': [...]}, # ids    of units to be scheduled
        #   }

        self._lock      = mt.RLock()   # lock on the bags
        self._units     = dict()       # unit registry (we use uids otherwise)
        self._unordered = list()       # IDs of units which are not colocated
        self._bags      = dict()       # nothing has run, yet

        self._bag_init  = {'size' : 0,
                           'uids' : list()}


    # --------------------------------------------------------------------------
    # overload the main method from the base class
    def _schedule_units(self, units):

        if not isinstance(units, list):
            units = [units]

        self.advance(units, rps.AGENT_SCHEDULING, publish=True, push=False)

        with self._lock:

            # cache ID int to avoid repeated parsing
            for unit in units:

                uid      = unit['uid']
                descr    = unit['description']
                colo_tag = descr.get('tags', {}).get('colocate')

                # units w/o order info are handled as usual, and we don't keep
                # any infos around
                if not colo_tag:
                  # self._log.debug('no tags for %s', uid)
                    self._unordered.append(unit)
                    continue

                # this uniit wants to be ordered - keep it in our registry
                assert(uid not in self._units), 'duplicated unit %s' % uid
                self._units[uid] = unit

                bag   = colo_tag['bag']
                size  = colo_tag['size']

              # self._log.debug('tags %s: %s : %d', uid, bag, size)

                # initiate bag if needed
                if bag not in self._bags:
                    self._bags[bag]         = copy.deepcopy(self._bag_init)
                    self._bags[bag]['size'] = size

                else:
                    assert(size == self._bags[bag]['size']), \
                           'inconsistent bag size'

                # add unit to order
                self._bags[bag]['uids'].append(uid)

        # try to schedule known units
        self._try_schedule()

        return True


    # --------------------------------------------------------------------------
    def _try_schedule(self):
        '''
        Schedule all units in self._unordered.  Then for all name spaces,
        check if their `current` order has units to schedule.  If not and
        we see `size` units are `done`, consider the order completed and go
        to the next one.  Break once we find a BoT which is not completely
        schedulable, either because we did not yet get all its units, or
        because we run out of resources to place those units.
        '''

        self._log.debug('try schedule')
        scheduled = list()  # list of scheduled units

        # FIXME: this lock is very aggressive, it should not be held over
        #        the scheduling algorithm's activity.
        # first schedule unordered units (
        with self._lock:

            keep = list()
            for unit in self._unordered:

                # attempt to schedule this unit (use continuous algorithm)
                if Continuous._try_allocation(self, unit):

                    # success - keep it and try the next one
                    scheduled.append(unit)

                else:
                    # failure - keep unit around
                    keep.append(unit)

            # keep only unscheduleed units
            self._unordered = keep


        # FIXME: this lock is very aggressive, it should not be held over
        #        the scheduling algorithm's activity.
        with self._lock:

            # now check all bags for eligibility, filter scheduled ones
            to_delete = list()
            for bag in self._bags:

                self._log.debug('try bag %s', bag)

                if self._bags[bag]['size'] < len(self._bags[bag]['uids']):
                    raise RuntimeError('inconsistent bag assembly')

                # if bag is complete, try to schedule it
                if self._bags[bag]['size'] == len(self._bags[bag]['uids']):

                    self._log.debug('try bag %s (full)', bag)
                    if self._try_schedule_bag(bag):

                        self._log.debug('try bag %s (placed)', bag)
                        # scheduling works - push units out and erase all traces
                        # of the bag (delayed until after iteration)
                        for uid in self._bags[bag]['uids']:

                            scheduled.append(self._units[uid])

                        to_delete.append(bag)

            # delete all bags which have been pushed out
            for bag in to_delete:

                del(self._bags[bag])


        # advance all scheduled units and push them out
        if scheduled:
            self.advance(scheduled, rps.AGENT_EXECUTING_PENDING,
                         publish=True, push=True)

      # self._log.debug('dump')
      # self._log.debug(pprint.pformat(self._bags))


    # --------------------------------------------------------------------------
    #
    def _try_schedule_bag(self, bag):
        '''
        This methods assembles the requiremets of all tasks in a bag into
        a single pseudo-unit.  We ask the cont scheduler to schedule that
        pseudo-unit for us.  If that works, we disassemble the resulting
        resource slots and assign them to the bag's units again, and declare
        success.
        '''

        self._log.debug('try schedule bag %s ', bag)

        tasks  = [self._units[uid] for uid in self._bags[bag]['uids']]
        pseudo = copy.deepcopy(tasks[0])

        pseudo['uid'] = 'pseudo.'

        descr = pseudo['description']
        descr['cpu_process_type'] = rpcud.POSIX  # force single node
        descr['cpu_thread_type']  = rpcud.POSIX
        descr['cpu_processes']    = 0
        descr['cpu_threads']      = 1

        descr['gpu_process_type'] = rpcud.POSIX  # force single node
        descr['gpu_thread_type']  = rpcud.POSIX
        descr['gpu_processes']    = 0
        descr['gpu_threads']      = 1

        self._log.debug('try schedule uids  %s ', self._bags[bag]['uids'])
      # self._log.debug('try schedule tasks  %s ', pprint.pformat(tasks))

        for task in tasks:
            td = task['description']
            pseudo['uid'] += task['uid']

            descr['cpu_processes'] += td['cpu_processes'] * td['cpu_threads']
            descr['gpu_processes'] += td['gpu_processes']

      # self._log.debug('try schedule pseudo %s ', pprint.pformat(pseudo))

        if not Continuous._try_allocation(self, pseudo):

            # cannot scshedule this pseudo task right now, bag has to wait
            return False

        # we got an allocation for the pseudo task, not dissassemble the slots
        # and assign back to the individual tasks in the bag
        slots = copy.deepcopy(pseudo['slots'])
        cpus  = copy.deepcopy(pseudo['slots']['nodes'][0]['core_map'])
        gpus  = copy.deepcopy(pseudo['slots']['nodes'][0]['gpu_map'])

        slots['nodes'][0]['core_map'] = list()
        slots['nodes'][0]['gpu_map']  = list()

        for task in tasks:

            tslots = copy.deepcopy(slots)
            descr  = task['description']

            for _ in range(descr['cpu_processes']):
                block = list()
                for _ in range(descr['cpu_threads']):
                    block.append(cpus.pop(0)[0])
                tslots['nodes'][0]['core_map'].append(block)

            for _ in range(descr['gpu_processes']):

                block = list()
                block.append(gpus.pop(0)[0])
                tslots['nodes'][0]['gpu_map'].append(block)

            task['slots'] = tslots

        return True


    # --------------------------------------------------------------------------
    #
    def schedule_cb(self, topic, msg):
        '''
        This cb gets triggered after some units got unscheduled, ie. their
        resources have been freed.  We attempt a new round of scheduling at that
        point.
        '''
        self._try_schedule()

        # keep the cb registered
        return True


# ------------------------------------------------------------------------------

