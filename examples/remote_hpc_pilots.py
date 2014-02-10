import sagapilot

# DBURL points to a MongoDB server. For installation of a MongoDB server, please
# refer to the MongoDB website: http://docs.mongodb.org/manual/installation/
DBURL  = "mongodb://ec2-184-72-89-141.compute-1.amazonaws.com:27017"

# RCONF points to the resource configuration files. Read more about resource 
# configuration files at http://saga-pilot.readthedocs.org/en/latest/machconf.html
RCONF  = ["https://raw.github.com/saga-project/saga-pilot/devel/configs/xsede.json",
          "https://raw.github.com/saga-project/saga-pilot/devel/configs/futuregrid.json"]

#-------------------------------------------------------------------------------
# 
if __name__ == "__main__":

    try:
        # Create a new session. A session is a set of Pilot Managers
        # and Unit Managers (with associated Pilots and ComputeUnits).
        session = sagapilot.Session(database_url=DBURL)
        print "Session UID: {0} ".format(session.uid)

        # Add an ssh identity to the session.
        cred = sagapilot.SSHCredential()
        cred.user_id = "tg802352"

        session.add_credential(cred)

        # Add a Pilot Manager with a machine configuration file for FutureGrid
        pmgr = sagapilot.PilotManager(session=session, resource_configurations=RCONF)

        # Define a 32-core on stamped that runs for 15 mintutes and 
        # uses $HOME/sagapilot.sandbox as sandbox directoy. 
        pdesc = sagapilot.ComputePilotDescription()
        pdesc.resource  = "stampede.tacc.utexas.edu"
        pdesc.runtime   = 15 # minutes
        pdesc.cores     = 32 

        # Launch the pilot.
        pilot = pmgr.submit_pilots(pdesc)
        print "Pilot UID       : {0} ".format( pilot.uid )

        # Create a workload of 8 '/bin/sleep' ComputeUnits (tasks)
        compute_units = []

        for unit_count in range(0, 32):
            cu = sagapilot.ComputeUnitDescription()
            cu.executable  = "/bin/hostname"
            cu.arguments   = ["-A"]
            cu.cores       = 1
        
            compute_units.append(cu)

        umgr = sagapilot.UnitManager(session=session, scheduler=sagapilot.SCHED_ROUND_ROBIN)
        umgr.add_pilots(pilot)
        
        umgr.submit_units(compute_units)

        # Wait for all compute units to finish.
        umgr.wait_units()

        for unit in umgr.get_units():
            print "* UID: {0}, STATE: {1}, START_TIME: {2}, STOP_TIME: {3}, EXEC_LOC: {4}".format(
                unit.uid, unit.state, unit.start_time, unit.stop_time, unit.execution_details)
        
            # Get the stdout and stderr streams of the ComputeUnit.
            print "  STDOUT: {0}".format(unit.stdout)
            print "  STDERR: {0}".format(unit.stderr)
        
        # Cancel all pilots.
        pmgr.cancel_pilots()

        session.destroy()

    except sagapilot.SagapilotException, ex:
        print "Error: %s" % ex


