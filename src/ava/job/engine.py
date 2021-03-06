# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

"""
Loading modules that provide task codes.
"""
import os
import time
import ast
import glob
import logging
import gevent
import uuid
from gevent import Greenlet

from .validator import ScriptValidator
from ava import launcher

from . import signals

logger = logging.getLogger(__name__)

_AVATARS_DIR = 'jobs'


class JobInfo(object):
    """ Metadata of a task definition
    """

    def __init__(self, name, script, acode):
        self._name = name
        self._script = script
        self._code = acode

    @property
    def name(self):
        """ Unique name for the task.
        :return: task's name
        """
        return self._name

    @property
    def script(self):
        return self._script

    @property
    def code(self):
        return self._code


class JobContext(object):
    """ The context of a task object.
    """

    _logger = logging.getLogger('ava.job')

    def __init__(self, job_name, core_context):
        self._job_name = job_name
        self._scope = {}
        self._core = core_context
        self._scope['ava'] = self
        self.exception = None
        self.result = None

    @property
    def name(self):
        return self._job_name

    @property
    def logger(self):
        return self._logger

    def notify_user(self, msg, title="Ava Message"):
        self._core.notify_user(msg, title)

    def sleep(self, secs):
        time.sleep(secs)


class JobRunner(Greenlet):
    def __init__(self, engine, job_info, job_context):
        Greenlet.__init__(self)
        self.engine = engine
        self.job_ctx = job_context
        self.job_info = job_info

    def _run(self):
        logger.info("Running job: %s", self.job_ctx.name)

        try:
            global_scope = dict()
            global_scope['__builtin__'] = {}

            exec self.job_info.code in global_scope, self.job_ctx._scope
            if 'result' in self.job_ctx._scope:
                self.job_ctx.result = self.job_ctx._scope.get('result')
        except Exception as ex:
            logger.error("Error in running job: %s", self.job_ctx.name, exc_info=True)
            self.job_ctx.exception = ex
        finally:
            self.engine.job_done(self.job_ctx)


class JobEngine(object):
    """
    Responsible for managing application modules.
    """

    def __init__(self):
        self.jobs = {}
        self.contexts = {}
        self.runners = {}

        self.jobs_path = os.path.join(launcher.get_app_dir(), _AVATARS_DIR)
        self.jobs_path = os.path.abspath(self.jobs_path)
        self.validator = ScriptValidator()
        self._core_context = None
        self._stopping = False

    def _scan_jobs(self):
        pattern = os.path.join(self.jobs_path, '[a-zA-Z][a-zA-Z0-9_]*.py')
        return glob.glob(pattern)

    def _load_jobs(self, ctx):
        logger.debug("Job directory: %s", self.jobs_path)

        job_files = self._scan_jobs()

        logger.debug("Found %d job(s)" % len(job_files))

        for s in job_files:
            name = os.path.basename(s)
            if '__init__.py' == name:
                continue

            # gets the basename without extension part.
            name = os.path.splitext(name)[0]
            try:
                logger.debug("Loading job: %s", name)
                with open(s, 'r') as f:
                    script = f.read()

                node = ast.parse(script, filename=name, mode='exec')
                self.validator.visit(node)
                acode = compile(node, filename=name, mode='exec')
                job_info = JobInfo(name, script, acode)
                self.jobs[name] = job_info
                self.contexts[name] = JobContext(name, self._core_context)
            except Exception:
                logger.error("Failed to load job: %s", name, exc_info=True)

    def _run_jobs(self):

        logger.debug("Starting jobs...")
        for task_name in self.jobs:
            info = self.jobs[task_name]
            ctx = self.contexts[task_name]
            runner = JobRunner(self, info, ctx)
            self.runners[task_name] = runner
            runner.start()

        while not self._stopping:
            time.sleep(1)

        logger.info("All jobs stopped.")

    def _gen_job_name(self):
        while True:
            name = 'J' + uuid.uuid1().hex[:8]
            if name not in self.jobs:
                return name

    def submit_job(self, job):
        job_name = self._gen_job_name()

        try:
            script = job['script']
            node = ast.parse(script, filename=job_name, mode='exec')
            self.validator.visit(node)
            acode = compile(node, filename=job_name, mode='exec')
            job_info = JobInfo(job_name, script, acode)
            self.jobs[job_name] = job_info
            ctx = JobContext(job_name, self._core_context)
            self.contexts[job_name] = ctx
            runner = JobRunner(self, job_info, ctx)
            self.runners[job_name] = runner
            runner.start()
            self._core_context.send(signals.JOB_ACCEPTED, job_name=job_name)
            return job_name
        except (Exception, SyntaxError) as ex:
            logger.error("Failed to run job: %s", job_name, exc_info=True)
            if ex.message is not None and len(ex.message) > 0:
                reason = ex.message
            else:
                reason = str(ex)

            print("REASON:", reason)
            self._core_context.send(signals.JOB_REJECTED, reason=reason)

    def job_done(self, job_context):
        """ Invoked by task runner to notify that a task is finished or failed.

        :param job_context:
        :return:
        """
        name = job_context.name
        if name in self.jobs:
            del self.jobs[name]

        if name in self.runners:
            del self.runners[name]

        if job_context.exception is not None:
            self._core_context.send(signals.JOB_FAILED, job_ctx=job_context)
        else:
            self._core_context.send(signals.JOB_FINISHED, job_ctx=job_context)

    def start(self, ctx):
        logger.debug("Starting job engine...")
        ctx.bind('jobengine', self)
        self._core_context = ctx
        self._load_jobs(ctx)
        ctx.add_child_greenlet(gevent.spawn(self._run_jobs))
        logger.debug("Job engine started.")

    def stop(self, ctx):
        self._stopping = True
        logger.debug("Job engine stopped.")
