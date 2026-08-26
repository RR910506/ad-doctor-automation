from __future__ import annotations

from contextlib import redirect_stderr,redirect_stdout
from io import StringIO
import logging
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


PROJECT_DIR=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT_DIR))

from src.security.resolver import PROJECT_DOTENV_PATH,build_secret_provider


class ProjectDotenvSecretLoadingTests(unittest.TestCase):
 def test_project_dotenv_path_is_deterministic(self):
  self.assertEqual(PROJECT_DOTENV_PATH,PROJECT_DIR/'.env')

 def test_gemini_key_resolves_from_ignored_dotenv(self):
  with tempfile.TemporaryDirectory() as temporary:
   dotenv=Path(temporary)/'.env';dotenv.write_text('GEMINI_API_KEY=DOTENV_TEST_VALUE\n',encoding='utf-8')
   with mock.patch.dict(os.environ,{},clear=True):
    result=build_secret_provider(PROJECT_DIR/'missing.db',dotenv_path=dotenv).resolve('GEMINI_API_KEY')
   self.assertTrue(result.configured);self.assertEqual(result.source,'secure_environment')

 def test_real_process_environment_overrides_dotenv(self):
  with tempfile.TemporaryDirectory() as temporary:
   dotenv=Path(temporary)/'.env';dotenv.write_text('GEMINI_API_KEY=DOTENV_LOWER_PRIORITY\n',encoding='utf-8')
   with mock.patch.dict(os.environ,{'GEMINI_API_KEY':'PROCESS_HIGHER_PRIORITY'},clear=True):
    result=build_secret_provider(PROJECT_DIR/'missing.db',dotenv_path=dotenv).resolve('GEMINI_API_KEY')
    self.assertEqual(result.value,'PROCESS_HIGHER_PRIORITY')

 def test_missing_dotenv_is_harmless(self):
  with tempfile.TemporaryDirectory() as temporary,mock.patch.dict(os.environ,{},clear=True):
   result=build_secret_provider(PROJECT_DIR/'missing.db',dotenv_path=Path(temporary)/'absent.env').resolve('GEMINI_API_KEY')
   self.assertFalse(result.configured)

 def test_secret_value_is_not_logged_or_written(self):
  marker='DOTENV_SECRET_MUST_NOT_APPEAR'
  with tempfile.TemporaryDirectory() as temporary:
   dotenv=Path(temporary)/'.env';dotenv.write_text(f'GEMINI_API_KEY={marker}\n',encoding='utf-8');stdout=StringIO();stderr=StringIO();records=[]
   class Capture(logging.Handler):
    def emit(self,record):records.append(self.format(record))
   handler=Capture();logging.getLogger().addHandler(handler)
   try:
    with mock.patch.dict(os.environ,{},clear=True),redirect_stdout(stdout),redirect_stderr(stderr):
     result=build_secret_provider(PROJECT_DIR/'missing.db',dotenv_path=dotenv).resolve('GEMINI_API_KEY')
    self.assertTrue(result.configured)
   finally:logging.getLogger().removeHandler(handler)
   self.assertNotIn(marker,stdout.getvalue());self.assertNotIn(marker,stderr.getvalue());self.assertNotIn(marker,' '.join(records))


if __name__=='__main__':unittest.main()
