"""No test run may touch a real CrossCast folder, where a stop file or a record would act on the person's own setup."""
import atexit
import os
import shutil
import tempfile

folder = tempfile.mkdtemp(prefix="crosscast-tests-")
os.environ["VPUB_HOME"] = folder
atexit.register(shutil.rmtree, folder, ignore_errors=True)
