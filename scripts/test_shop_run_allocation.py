"""Unique allocation regression, synthetic filesystem only, no model calls."""
import tempfile,unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from types import SimpleNamespace
import run_codex_role as m
class AllocationTests(unittest.TestCase):
 def setUp(self):self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name)
 def tearDown(self):self.t.cleanup()
 def test_parallel_frozen_clock(self):
  with patch.object(m,'datetime') as clock:
   clock.now.return_value.strftime.return_value='frozen'
   with ThreadPoolExecutor(max_workers=16) as pool:
    rows=list(pool.map(lambda _:m.allocate_run_directory(self.root,'douyin-shop-ops'),range(100)))
  self.assertEqual(len(set(x[0] for x in rows)),100)
  self.assertTrue(all(p.is_dir() for _,p in rows))
 def test_collision_retries_never_overwrites(self):
  with patch.object(m,'datetime') as clock,patch.object(m.uuid,'uuid4',side_effect=[SimpleNamespace(hex='same'),SimpleNamespace(hex='same'),SimpleNamespace(hex='new')]):
   clock.now.return_value.strftime.return_value='frozen'
   _,a=m.allocate_run_directory(self.root,'douyin-shop-ops');(a/'sentinel').write_text('preserve')
   _,b=m.allocate_run_directory(self.root,'douyin-shop-ops')
  self.assertNotEqual(a,b);self.assertEqual((a/'sentinel').read_text(),'preserve')
 def test_bounded_collision_failure(self):
  with patch.object(m,'datetime') as clock,patch.object(m.uuid,'uuid4',return_value=SimpleNamespace(hex='same')):
   clock.now.return_value.strftime.return_value='frozen'
   _,a=m.allocate_run_directory(self.root,'douyin-shop-ops');(a/'sentinel').write_text('preserve')
   with self.assertRaisesRegex(RuntimeError,'8 collisions'):m.allocate_run_directory(self.root,'douyin-shop-ops')
  self.assertEqual((a/'sentinel').read_text(),'preserve')
 def test_permission_error_not_retried_as_collision(self):
  with patch.object(Path,'mkdir',side_effect=PermissionError('denied')) as mk:
   with self.assertRaises(PermissionError):m.allocate_run_directory(self.root,'douyin-shop-ops')
   self.assertEqual(mk.call_count,1)
if __name__=='__main__':unittest.main(verbosity=2)
