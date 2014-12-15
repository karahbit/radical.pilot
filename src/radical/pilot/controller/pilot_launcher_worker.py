"""
.. module:: radical.pilot.controller.pilot_launcher_worker
.. moduleauthor:: Ole Weidner <ole.weidner@rutgers.edu>
"""

__copyright__ = "Copyright 2013-2014, http://radical.rutgers.edu"
__license__ = "MIT"

import os
import copy
import time
import saga
import datetime
import traceback
import threading

from bson.objectid import ObjectId

from radical.pilot.states import *

from radical.pilot.utils.version import version as VERSION
from radical.pilot.utils.logger  import logger
from radical.pilot.context       import Context
from radical.pilot.logentry      import Logentry


IDLE_TIMER           = 0.1 # seconds to sleep if notthing to do
JOB_CHECK_INTERVAL   = 60  # seconds between runs of the job state check loop
JOB_CHECK_MAX_MISSES =  3  # number of times to find a job missing before
                           # declaring it dead

DEFAULT_AGENT_TYPE    = 'multicore'
DEFAULT_AGENT_VERSION = 'stage@local'
DEFAULT_VIRTENV       = '%(pilot_sandbox)s/virtenv'
DEFAULT_VIRTENV_MODE  = 'private'

# ----------------------------------------------------------------------------
#
class PilotLauncherWorker(threading.Thread):
    """PilotLauncherWorker handles bootstrapping and launching of
       the pilot agents.
    """

    # ------------------------------------------------------------------------
    #
    def __init__(self, session, db_connection_info, pilot_manager_id,
                 shared_worker_data, number=None):
        """Creates a new pilot launcher background process.
        """
        self._session = session

        # threading stuff
        threading.Thread.__init__(self)

        self.db_connection_info = db_connection_info
        self.pilot_manager_id   = pilot_manager_id
        self.name               = "PilotLauncherWorker-%s" % str(number)
        self.missing_pilots     = dict()
        self._shared_worker_data = shared_worker_data

        # Stop event can be set to terminate the main loop
        self._stop = threading.Event()
        self._stop.clear()

    # ------------------------------------------------------------------------
    #
    def stop(self):
        """stop() signals the process to finish up and terminate.
        """
        logger.error("launcher %s stopping" % (self.name))
        self._stop.set()
        self.join()
        logger.error("launcher %s stopped" % (self.name))
      # logger.debug("Launcher thread (ID: %s[%s]) for PilotManager %s stopped." %
      #             (self.name, self.ident, self.pilot_manager_id))


    # ------------------------------------------------------------------------
    #
    def _get_pilot_logs (self, pilot_col, pilot_id) :

        out, err, log = ["", "", ""]
        return out, err, log

        # attempt to get stdout/stderr/log.  We only expect those if pilot was
        # attemptint launch at some point
        launched = False
        pilot    = pilot_col.find ({"_id": pilot_id})[0]

        for entry in pilot['statehistory'] :
            if entry['state'] == LAUNCHING :
                launched = True
                break

        if  launched :
            MAX_IO_LOGLENGTH = 10240    # 10k should be enough for anybody...

            try :
                f_out = saga.filesystem.File ("%s/%s" % (pilot['sandbox'], 'AGENT.STDOUT'))
                out   = f_out.read()[-MAX_IO_LOGLENGTH:]
                f_out.close ()
            except :
                pass

            try :
                f_err = saga.filesystem.File ("%s/%s" % (pilot['sandbox'], 'AGENT.STDERR'))
                err   = f_err.read()[-MAX_IO_LOGLENGTH:]
                f_err.close ()
            except :
                pass

            try :
                f_log = saga.filesystem.File ("%s/%s" % (pilot['sandbox'], 'AGENT.LOG'))
                log   = f_log.read()[-MAX_IO_LOGLENGTH:]
                f_log.close ()
            except :
                pass

        return out, err, log


    # --------------------------------------------------------------------------
    #
    def check_pilot_states (self, pilot_col) :

        pending_pilots = pilot_col.find(
            {"pilotmanager": self.pilot_manager_id,
             "state"       : {"$in": [PENDING_ACTIVE, ACTIVE]}}
        )

        for pending_pilot in pending_pilots:

            pilot_failed = False
            pilot_done   = False
            reconnected  = False
            pilot_id     = pending_pilot["_id"]
            log_message  = ""
            saga_job_id  = pending_pilot["saga_job_id"]

            logger.info("Performing periodical health check for %s (SAGA job id %s)" % (str(pilot_id), saga_job_id))

            if  not pilot_id in self.missing_pilots :
                self.missing_pilots[pilot_id] = 0

            # Create a job service object:
            try:
                js_url = saga_job_id.split("]-[")[0][1:]

                if  js_url in self._shared_worker_data['job_services'] :
                    js = self._shared_worker_data['job_services'][js_url]
                else :
                    js = saga.job.Service(js_url, session=self._session)
                    self._shared_worker_data['job_services'][js_url] = js

                saga_job     = js.get_job(saga_job_id)
                reconnected  = True

                if  saga_job.state in [saga.job.FAILED, saga.job.CANCELED] :
                    pilot_failed = True
                    log_message  = "SAGA job state for ComputePilot %s is %s."\
                                 % (pilot_id, saga_job.state)

                if  saga_job.state in [saga.job.DONE] :
                    pilot_done = True
                    log_message  = "SAGA job state for ComputePilot %s is %s."\
                                 % (pilot_id, saga_job.state)

            except Exception as e:

                if  not reconnected :
                    logger.warning ('could not reconnect to pilot for state check (%s)' % e)
                    self.missing_pilots[pilot_id] += 1

                    if  self.missing_pilots[pilot_id] >= JOB_CHECK_MAX_MISSES :
                        logger.error ('giving up after 10 attempts')
                        pilot_failed = True
                        log_message  = "Could not reconnect to pilot %s "\
                                       "multiple times - giving up" % pilot_id
                else :
                    logger.warning ('pilot state check failed: %s' % e)
                    pilot_failed = True
                    log_message  = "Couldn't determine job state for ComputePilot %s. " \
                                   "Assuming it has failed." % pilot_id


            if  pilot_failed :
                out, err, log = self._get_pilot_logs (pilot_col, pilot_id)
                ts = datetime.datetime.utcnow()
                pilot_col.update(
                    {"_id"  : pilot_id,
                     "state": {"$ne"     : DONE}},
                    {"$set" : {
                        "state"          : FAILED,
                        "stdout"         : out,
                        "stderr"         : err,
                        "logfile"        : log
                        },
                     "$push": {
                         "statehistory"  : {
                             "state"     : FAILED,
                             "timestamp" : ts
                             },
                         "log": {
                             "message"   : log_message,
                             "timestamp" : ts
                             }
                         }
                     }
                )
                logger.error (log_message)
                logger.error ('pilot %s declared dead' % pilot_id)


            elif pilot_done :
                # FIXME: this should only be done if the state is not yet
                # done...
                out, err, log = self._get_pilot_logs (pilot_col, pilot_id)
                ts = datetime.datetime.utcnow()
                pilot_col.update(
                    {"_id"  : pilot_id,
                     "state": {"$ne"     : DONE}},
                    {"$set" : {
                        "state"          : DONE,
                        "stdout"         : out,
                        "stderr"         : err,
                        "logfile"        : log},
                     "$push": {
                         "statehistory"  : {
                             "state"     : DONE,
                             "timestamp" : ts
                             },
                         "log": {
                             "message"   : log_message,
                             "timestamp" : ts
                             }
                         }
                     }
                )
                logger.error (log_message)
                logger.error ('pilot %s declared dead' % pilot_id)

            else :
                if self.missing_pilots[pilot_id] :
                    logger.info ('pilot %s *assumed* alive and well (%s)' \
                              % (pilot_id, self.missing_pilots[pilot_id]))
                else :
                    logger.info ('pilot %s seems alive and well' \
                              % (pilot_id))


    # ------------------------------------------------------------------------
    #
    def run(self):
        """Starts the process when Process.start() is called.
        """

        # make sure to catch sys.exit (which raises SystemExit)
        try :
            # Get directory where this module lives
            mod_dir = os.path.dirname(os.path.realpath(__file__))

            # Try to connect to the database
            try:
                connection = self.db_connection_info.get_db_handle()
                db = connection[self.db_connection_info.dbname]
                pilot_col = db["%s.p" % self.db_connection_info.session_id]
                logger.debug("Connected to MongoDB. Serving requests for PilotManager %s." % self.pilot_manager_id)

            except Exception, ex:
                tb = traceback.format_exc()
                logger.error("Connection error: %s. %s" % (str(ex), tb))
                return

            last_job_check = time.time()

            while not self._stop.is_set():

                # Periodically, we pull up all ComputePilots that are pending 
                # execution or were last seen executing and check if the corresponding  
                # SAGA job is still pending in the queue. If that is not the case, 
                # we assume that the job has failed for some reasons and update
                # the state of the ComputePilot accordingly.
                if  last_job_check + JOB_CHECK_INTERVAL < time.time() :
                    last_job_check = time.time()
                    self.check_pilot_states (pilot_col)


                # See if we can find a ComputePilot that is waiting to be launched.
                # If we find one, we use SAGA to create a job service, a job
                # description and a job that is then send to the local or remote
                # queueing system. If this succedes, we set the ComputePilot's
                # state to pending, otherwise to failed.
                compute_pilot = None

                ts = datetime.datetime.utcnow()
                compute_pilot = pilot_col.find_and_modify(
                    query={"pilotmanager": self.pilot_manager_id,
                           "state" : PENDING_LAUNCH},
                    update={"$set" : {"state": LAUNCHING},
                            "$push": {"statehistory": {"state": LAUNCHING, "timestamp": ts}}}
                )

                if  not compute_pilot :
                    time.sleep(IDLE_TIMER)

                else:
                    try:
                        # ------------------------------------------------------
                        #
                        # LAUNCH THE PILOT AGENT VIA SAGA
                        #
                        logentries = []
                        pilot_id   = str(compute_pilot["_id"])

                        logger.info("Launching ComputePilot %s" % pilot_id)


                        # ------------------------------------------------------
                        # Database connection parameters
                        session_uid   = self.db_connection_info.session_id
                        database_url  = self.db_connection_info.dburl
                        database_name = self.db_connection_info.dbname

                        db_url = saga.Url (database_url)

                        # set default host, port and dbname
                        if not db_url.port  : db_url.port   = 27017
                        if not db_url.host  : db_url.host   = 'localhost'
                        if not database_name: database_name = 'radicalpilot'

                        database_auth     = self.db_connection_info.dbauth
                        database_hostport = "%s:%d" % (db_url.host, db_url.port)


                        # ------------------------------------------------------
                        # pilot desxcription and resorce configuration
                        number_cores   = compute_pilot['description']['cores']
                        runtime        = compute_pilot['description']['runtime']
                        queue          = compute_pilot['description']['queue']
                        project        = compute_pilot['description']['project']
                        cleanup        = compute_pilot['description']['cleanup']
                        resource_key   = compute_pilot['description']['resource']
                        schema         = compute_pilot['description']['access_schema']
                        memory         = compute_pilot['description']['memory']
                        pilot_sandbox  = compute_pilot['sandbox']
                        global_sandbox = compute_pilot['global_sandbox']


                        # we expand and exchange keys in the resource config,
                        # depending on the selected schema so better use a deep
                        # copy..
                        resource_cfg = self._session.get_resource_config(resource_key, schema)

                        # ------------------------------------------------------
                        # get parameters from cfg, set defaults where needed
                        agent_mongodb_endpoint  = resource_cfg.get ('agent_mongodb_endpoint', db_url)
                        agent_scheduler         = resource_cfg.get ('agent_scheduler')
                        default_queue           = resource_cfg.get ('default_queue')
                        forward_tunnel_endpoint = resource_cfg.get ('forward_tunnel_endpoint')
                        js_endpoint             = resource_cfg.get ('job_manager_endpoint')
                        lrms                    = resource_cfg.get ('lrms')
                        mpi_launch_method       = resource_cfg.get ('mpi_launch_method')
                        agent_type              = resource_cfg.get ('pilot_agent_type',    DEFAULT_AGENT_TYPE)
                        agent_version           = resource_cfg.get ('pilot_agent_version', DEFAULT_AGENT_VERSION)
                        pre_bootstrap           = resource_cfg.get ('pre_bootstrap')
                        python_interpreter      = resource_cfg.get ('python_interpreter')
                        spmd_variation          = resource_cfg.get ('spmd_variation')
                        task_launch_method      = resource_cfg.get ('task_launch_method')
                        virtenv_mode            = resource_cfg.get ('virtenv_mode',        DEFAULT_VIRTENV_MODE)
                        virtenv                 = resource_cfg.get ('virtenv',             DEFAULT_VIRTENV)

                        # deprecated
                        global_virtenv          = resource_cfg.get ('global_virtenv')
                        if  global_virtenv :
                            logger.warn ("'global_virtenv' keyword is deprecated -- use 'virtenv' and 'virtenv_mode'")
                            virtenv      = global_virtenv
                            virtenv_mode = 'create'


                        # expand variables in virtenv string
                        virtenv = virtenv % {'pilot_sandbox' : saga.Url(pilot_sandbox).path, 
                                             'global_sandbox': saga.Url(global_sandbox).path }  

                        # ------------------------------------------------------
                        # Copy the bootstrap shell script.  This also creates
                        # the sandbox. We use always "default_bootstrapper.sh"
                        bootstrapper = 'default_bootstrapper.sh'
                        bootstrapper_path = os.path.abspath("%s/../bootstrapper/%s" \
                                % (mod_dir, bootstrapper))

                        msg = "Using bootstrapper %s" % bootstrapper_path
                        logentries.append (Logentry (msg, logger=logger.info))


                        bs_script_url = saga.Url("file://localhost/%s" % bootstrapper_path)
                        bs_script_tgt = saga.Url("%s/pilot_bootstrapper.sh" % pilot_sandbox)

                        msg = "Copying bootstrapper '%s' to agent sandbox (%s)." \
                                % (bs_script_url, bs_script_tgt)
                        logentries.append(Logentry (msg, logger=logger.debug))

                        bs_script = saga.filesystem.File(bs_script_url, session=self._session)
                        bs_script.copy(bs_script_tgt, flags=saga.filesystem.CREATE_PARENTS)
                        bs_script.close()


                        # ------------------------------------------------------
                        # the version of the agent is derived from
                        # pilot_agent_version, which has the following format
                        # and interpretation:
                        #
                        # format: mode@source
                        #
                        # mode     :
                        #   virtenv: use pilot agent as installed in the
                        #            virtenv on the target resource
                        #   stage  : stage pilot agent from local to target
                        #            resource
                        #
                        # sourcen  :
                        #   tag    : a git tag
                        #   branch : a git branch
                        #   release: pypi release
                        #   local  : locally installed version
                        #   path   : a specific file on localhost
                        #
                        # examples :
                        #   virtenv@v0.20
                        #   virtenv@devel
                        #   virtenv@release
                        #   stage@local
                        #   stage@/tmp/my_agent.py
                        #
                        # Note that some combinations may be invalid,
                        # specifically in the context of virtenv_mode.  If, for
                        # example, virtenv_mode is 'use', then the 'virtenv:tag'
                        # will not make sense, as the virtenv is not updated.
                        # In those cases, the virtenv_mode is honored, and
                        # a warning is printed.
                        #
                        # Also, the 'stage' mode can only be combined with the
                        # 'local' source, or with a path to the agent (relative
                        # to mod_dir, or absolute).
                        #
                        # A pilot_agent_version which does not adhere to the
                        # above syntax is ignored, and the fallback stage@local
                        # is used.
                        
                        if not '@' in agent_version :
                            logger.warn ("invalid pilot_agent_version '%s', using default '%s'" \
                                      % (agent_version, DEFAULT_AGENT_VERSION))
                            agent_version = DEFAULT_AGENT_VERSION


                        agent_mode, agent_source = agent_version.split ('@', 1)

                        if not agent_mode or not agent_source :
                            logger.warn ("invalid pilot_agent_version '%s', using default '%s'" \
                                      % (agent_version, DEFAULT_AGENT_VERSION))
                            agent_version = DEFAULT_AGENT_VERSION
                            agent_mode, agent_source = agent_version.split ('@', 1)


                        if not agent_mode in ['stage', 'virtenv'] :
                            logger.error ("invalid pilot_agent_version '%s', using default '%s'" \
                                      % (agent_version, DEFAULT_AGENT_VERSION))
                            agent_version = DEFAULT_AGENT_VERSION
                            agent_mode, agent_source = agent_version.split ('@', 1)



                        # we only stage the agent on agent_mode==stage --
                        # otherwise the bootstrapper will have to take care of
                        # it
                        if agent_mode == 'stage' :

                            # staging can handle 'local', which is the old
                            # behavior of using the locally installed agent, or
                            # a path, which we expect to be specified if the
                            # agent_source!=local
                            if  agent_source == 'local' :
                                agent_name = "radical-pilot-agent-%s.py" % agent_type
                                agent_path = os.path.abspath("%s/../agent/%s" % (mod_dir, agent_name))

                            else :
                                if  agent_source.startswith ('/') :
                                    agent_path = agent_source
                                else :
                                    agent_path = os.path.abspath("%s/%s" % (mod_dir, agent_source))

                            msg = "Using pilot agent %s" % agent_path
                            logentries.append (Logentry (msg, logger=logger.info))

                            # --------------------------------------------------
                            # Copy the agent script
                            #
                            agent_url = saga.Url("file://localhost/%s" % agent_path)
                            msg = "Copying agent '%s' to agent sandbox (%s)." % (agent_url, pilot_sandbox)
                            logentries.append(Logentry (msg, logger=logger.debug))

                            agent_file = saga.filesystem.File(agent_url)
                            agent_file.copy("%s/radical-pilot-agent.py" % str(pilot_sandbox))
                            agent_file.close()


                            # if the agent was staged, we tell the bootstrapper
                            agent_version = 'stage'

                        else :  # agent_mode == 'virtenv' :
                            # otherwise, we let the bootstrapper know what
                            # version to use
                            agent_version = agent_source


                        # ------------------------------------------------------
                        # sanity checks
                        if not agent_scheduler    : raise RuntimeError("missing agent scheduler")
                        if not lrms               : raise RuntimeError("missing LRMS")
                        if not mpi_launch_method  : raise RuntimeError("missing mpi launch method")
                        if not task_launch_method : raise RuntimeError("missing task launch method")

                        # massage some values
                        debug_level = os.environ.get ('RADICAL_PILOT_AGENT_VERBOSE', logger.level)
                        debug_level = { 'CRITICAL' : 1,
                                        'ERROR'    : 2,
                                        'WARNING'  : 3,
                                        'WARN'     : 3,
                                        'INFO'     : 4,
                                        'DEBUG'    : 5}.get (debug_level, int(debug_level))

                        if not queue :
                            queue = default_queue

                        if  cleanup and isinstance (cleanup, bool) :
                            cleanup = 'luve'    #  l : log files
                                                #  u : unit work dirs
                                                #  v : virtualenv
                                                #  e : everything (== pilot sandbox)


                        # set mandatory args
                        bootstrap_args  = ""
                        bootstrap_args += " -a '%s'" % database_auth
                        bootstrap_args += " -c '%s'" % number_cores
                        bootstrap_args += " -d '%s'" % debug_level
                        bootstrap_args += " -g '%s'" % virtenv
                        bootstrap_args += " -j '%s'" % task_launch_method
                        bootstrap_args += " -k '%s'" % mpi_launch_method
                        bootstrap_args += " -l '%s'" % lrms
                        bootstrap_args += " -m '%s'" % database_hostport
                        bootstrap_args += " -n '%s'" % database_name
                        bootstrap_args += " -p '%s'" % pilot_id
                        bootstrap_args += " -q '%s'" % agent_scheduler
                        bootstrap_args += " -r '%s'" % runtime
                        bootstrap_args += " -s '%s'" % session_uid
                        bootstrap_args += " -t '%s'" % agent_name
                        bootstrap_args += " -u '%s'" % virtenv_mode
                        bootstrap_args += " -v '%s'" % agent_version

                        # set optional args
                        if cleanup                 : bootstrap_args += " -x '%s'" % cleanup
                        if forward_tunnel_endpoint : bootstrap_args += " -f '%s'" % forward_tunnel_endpoint
                        if pre_bootstrap           : bootstrap_args += " -e '%s'" % "' -e '".join (pre_bootstrap)
                        if python_interpreter      : bootstrap_args += " -i '%s'" % python_interpreter


                        # ------------------------------------------------------
                        # now that the script is in place and we know where it is,
                        # we can launch the agent
                        js_url = saga.Url(js_endpoint)
                        logger.debug ("saga.job.Service ('%s')" % js_url)
                        if  js_url in self._shared_worker_data['job_services'] :
                            js = self._shared_worker_data['job_services'][js_url]
                        else :
                            js = saga.job.Service(js_url, session=self._session)
                            self._shared_worker_data['job_services'][js_url] = js


                        # ------------------------------------------------------
                        # Create SAGA Job description and submit the pilot job

                        jd = saga.job.Description()

                        jd.executable            = "/bin/bash"
                        jd.arguments             = ["-l pilot_bootstrapper.sh", bootstrap_args]
                        jd.spmd_variation        = spmd_variation
                        jd.working_directory     = saga.Url(pilot_sandbox).path
                        jd.project               = project
                        jd.output                = "AGENT.STDOUT"
                        jd.error                 = "AGENT.STDERR"
                        jd.total_cpu_count       = number_cores
                        jd.wall_time_limit       = runtime
                        jd.total_physical_memory = memory
                        jd.queue                 = queue

                        if 'RADICAL_PILOT_PROFILE' in os.environ :
                            jd.environment = {'RADICAL_PILOT_PROFILE' : 'TRUE'}

                        logger.debug("Bootstrap command line: %s %s" % (jd.executable, jd.arguments))

                        msg = "Submitting SAGA job with description: %s" % str(jd.as_dict())
                        logentries.append(Logentry (msg, logger=logger.debug))

                        pilotjob = js.create_job(jd)
                        pilotjob.run()

                        # do a quick error check
                        if pilotjob.state == saga.FAILED:
                            raise RuntimeError ("SAGA Job state is FAILED.")

                        saga_job_id = pilotjob.id
                        self._shared_worker_data['job_ids'][pilot_id] = [saga_job_id, js_url]

                        msg = "SAGA job submitted with job id %s" % str(saga_job_id)
                        logentries.append(Logentry (msg, logger=logger.debug))

                        #
                        # ------------------------------------------------------

                        log_dicts = list()
                        for le in logentries :
                            log_dicts.append (le.as_dict())

                        # Update the Pilot's state to 'PENDING_ACTIVE' if SAGA job submission was successful.
                        ts = datetime.datetime.utcnow()
                        ret = pilot_col.update(
                            {"_id"  : ObjectId(pilot_id),
                             "state": 'Launching'},
                            {"$set" : {"state": PENDING_ACTIVE,
                                      "saga_job_id": saga_job_id},
                             "$push": {"statehistory": {"state": PENDING_ACTIVE, "timestamp": ts}},
                             "$pushAll": {"log": log_dicts}
                            }
                        )

                        if  ret['n'] == 0 :
                            # could not update, probably because the agent is
                            # running already.  Just update state history and
                            # jobid then
                            # FIXME: make sure of the agent state!
                            ret = pilot_col.update(
                                {"_id"  : ObjectId(pilot_id)},
                                {"$set" : {"saga_job_id": saga_job_id},
                                 "$push": {"statehistory": {"state": PENDING_ACTIVE, "timestamp": ts}},
                                 "$pushAll": {"log": log_dicts}}
                            )


                    except Exception, ex:
                        # Update the Pilot's state 'FAILED'.
                        out, err, log = self._get_pilot_logs (pilot_col, pilot_id)
                        ts = datetime.datetime.utcnow()

                        # FIXME: we seem to be unable to bson/json handle saga
                        # log messages containing an '#'.  This shows up here.
                        # Until we find a clean workaround, make log shorter and
                        # rely on saga logging to reveal the problem.
                      # msg = "Pilot launching failed: %s\n%s" % (str(ex), traceback.format_exc())
                        msg = "Pilot launching failed!"
                        logentries.append (Logentry (msg))

                        log_dicts    = list()
                        log_messages = list()
                        for le in logentries :
                            log_dicts.append (le.as_dict())
                            log_messages.append (le.message)

                        pilot_col.update(
                            {"_id"  : ObjectId(pilot_id),
                             "state": {"$ne" : FAILED}},
                            {"$set" : {
                                "state"   : FAILED,
                                "stdout"  : out,
                                "stderr"  : err,
                                "logfile" : log},
                             "$push": {"statehistory": {"state"    : FAILED,
                                                        "timestamp": ts}},
                             "$pushAll": {"log": log_dicts}}
                        )
                        logger.exception ('\n'.join (log_messages))

        except SystemExit as e :
            logger.exception("pilot launcher thread caught system exit -- forcing application shutdown")
            import thread
            thread.interrupt_main ()


