
import os
import sys
import radical.pilot as rp
import traceback

""" DESCRIPTION: Tutorial 2: Chaining Tasks.
For every task A_n a task B_n is started consecutively.
"""

# READ: The RADICAL-Pilot documentation: 
#   http://radicalpilot.readthedocs.org/en/latest
#
# Try running this example with RADICAL_PILOT_VERBOSE=debug set if 
# you want to see what happens behind the scences!


#------------------------------------------------------------------------------
#
def pilot_state_cb (pilot, state) :
    """ this callback is invoked on all pilot state changes """

    print "[Callback]: ComputePilot '%s' state: %s." % (pilot.uid, state)

    if  state == rp.FAILED :
        sys.exit (1)


#------------------------------------------------------------------------------
#
def unit_state_cb (unit, state) :
    """ this callback is invoked on all unit state changes """

    print "[Callback]: ComputeUnit  '%s' state: %s." % (unit.uid, state)

    if  state == rp.FAILED :
        sys.exit (1)


# -----------------------------------------------------------
#
def main():

    try:
        # Create a new session. A session is the 'root' object for all other
        # RADICAL-Pilot objects. It encapsulates the MongoDB connection(s) as
        # well as security contexts.
        session = rp.Session()

        # Add an ssh identity to the session.
        c = rp.Context('ssh')
      # c.user_id = 'osdcXX'
        session.add_context(c)

        # Add a Pilot Manager. Pilot managers manage one or more ComputePilots.
        print "Initializing Pilot Manager ..."
        pmgr = rp.PilotManager(session=session)

        # Register our callback with the PilotManager. This callback will get
        # called every time any of the pilots managed by the PilotManager
        # change their state.
        pmgr.register_callback(pilot_state_cb)

        # this describes the parameters and requirements for our pilot job
        pdesc = rp.ComputePilotDescription ()
        pdesc.resource = 'localhost'
        pdesc.runtime  =  5 # minutes
        pdesc.cores    =  1
        pdesc.cleanup  =  True

        # submit the pilot.
        print "Submitting Compute Pilot to Pilot Manager ..."
        pilot = pmgr.submit_pilots(pdesc)

        # Combine the ComputePilot, the ComputeUnits and a scheduler via
        # a UnitManager object.
        print "Initializing Unit Manager ..."
        umgr = rp.UnitManager(
            session=session,
            scheduler=rp.SCHED_DIRECT_SUBMISSION)

        # Register our callback with the UnitManager. This callback will get
        # called every time any of the units managed by the UnitManager
        # change their state.
        umgr.register_callback(unit_state_cb)

        # Add the previously created ComputePilot to the UnitManager.
        print "Registering Compute Pilot with Unit Manager ..."
        umgr.add_pilots(pilot)

        NUMBER_JOBS  = 10 # the total number of cus to run

        # submit A cus to pilot job
        cudesc_list_A = []
        for i in range(NUMBER_JOBS):

            # -------- BEGIN USER DEFINED CU A_n DESCRIPTION --------- #
            cudesc = rp.ComputeUnitDescription()
            cudesc.environment = {"CU_LIST": "A", "CU_NO": "%02d" % i}
            cudesc.executable  = "/bin/echo"
            cudesc.arguments   = ['"$CU_LIST CU with id $CU_NO"']
            cudesc.cores       = 1
            # -------- END USER DEFINED CU A_n DESCRIPTION --------- #

            cudesc_list_A.append(cudesc)

        # Submit the previously created ComputeUnit descriptions to the
        # PilotManager. This will trigger the selected scheduler to start
        # assigning ComputeUnits to the ComputePilots.
        print "Submit 'A' Compute Units to Unit Manager ..."
        cu_list_A = umgr.submit_units(cudesc_list_A)

        # Chaining cus i.e submit a compute unit, when compute unit from A is successfully executed.
        # A B CU reads the content of the output file of an A CU and writes it into its own
        # output file.
        cu_list_B = []
        # We create a copy of cu_list_A so that we can remove elements from it,
        # and still reference to the original index.
        cu_list_A_copy = cu_list_A[:]
        while cu_list_A:
            for cu_a in cu_list_A:
                idx = cu_list_A_copy.index(cu_a)

                cu_a.wait ()
                print "'A' Compute Unit '%s' finished. Submitting 'B' CU ..." % idx

                # -------- BEGIN USER DEFINED CU B_n DESCRIPTION --------- #
                cudesc = rp.ComputeUnitDescription()
                cudesc.environment = {'CU_LIST': 'B', 'CU_NO': "%02d" % idx}
                cudesc.executable  = '/bin/echo'
                cudesc.arguments   = ['"$CU_LIST CU with id $CU_NO"']
                cudesc.cores       = 1
                # -------- END USER DEFINED CU B_n DESCRIPTION --------- #

                # Submit CU to Pilot Job
                cu_b = umgr.submit_units(cudesc)
                cu_list_B.append(cu_b)
                cu_list_A.remove(cu_a)

        print "Waiting for 'B' Compute Units to complete ..."
        for cu_b in cu_list_B :
            cu_b.wait ()
            print "'B' Compute Unit '%s' finished with output:" % (cu_b.uid)
            print cu_b.stdout

        print "All Compute Units completed successfully!"

        session.close()
        print "Closed session, exiting now ..."

    except Exception as e:
            print "AN ERROR OCCURRED: %s" % ((str(e)))
            return(-1)


#------------------------------------------------------------------------------
#
if __name__ == "__main__":

    sys.exit(main())

#
#------------------------------------------------------------------------------

