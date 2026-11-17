import importlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'client'))
import engine
import settings

REPO = 'MIAOKATZE/GT-Interesting-Thing'

def make_jar(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\n')
        z.writestr('test/' + payload + '.class', payload.encode())

class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'instance' / '.minecraft'
        (self.root / 'mods').mkdir(parents=True)
        (self.root / 'config').mkdir()
        (self.root / 'options.txt').write_text('gamma:0.8\n')
        (self.root / 'config' / 'my-settings.cfg').write_text('volume=1\n')
        self.old = self.root / 'mods' / 'gtit-1.0.jar'
        self.new = self.root / 'mods' / 'gtit-2.0.jar'
        make_jar(self.old, 'old')
        self.payload = Path(self.temp.name) / 'official.jar'
        make_jar(self.payload, 'new')
        self.manifest = {
            'schema_version': 1, 'pack_id': 'gtnh-private-addons', 'gtnh_version':'2.9.0-final',
            'revision':'rev1', 'mods': [{
                'id':'gtit', 'repo':REPO, 'name':'GT Interesting Thing',
                'version':'v2.0', 'filename':self.new.name,
                'url':'https://github.com/MIAOKATZE/GT-Interesting-Thing/releases/download/v2.0/gtit-2.0.jar',
                'sha256':engine.sha256(self.payload), 'side':'both',
                'installed_regex':r'(?i)^gtit[-_.].*\.jar$'}]
        }
        self.addCleanup(lambda: None)

    def download(self, mod, dest):
        import shutil
        shutil.copy2(self.payload, dest)

    def test_detect_initial_update_and_preserve_settings(self):
        changes = engine.inspect(self.root, self.manifest)
        self.assertEqual(changes[0]['action'], 'reemplazar')
        txn = engine.apply(self.root, self.manifest, confirm_closed=True,
                           allow_first_install=True, downloader=self.download)
        self.assertIsInstance(txn, str)
        self.assertFalse(self.old.exists())
        self.assertEqual(engine.sha256(self.new), engine.sha256(self.payload))
        self.assertEqual((self.root/'options.txt').read_text(), 'gamma:0.8\n')
        self.assertEqual((self.root/'config'/'my-settings.cfg').read_text(), 'volume=1\n')
        self.assertEqual(engine.inspect(self.root, self.manifest)[0]['action'], 'igual')

    def test_rollback_restore_original_jar(self):
        txn = engine.apply(self.root, self.manifest, confirm_closed=True,
                           allow_first_install=True, downloader=self.download)
        self.assertEqual(engine.rollback(self.root, txn), txn)
        self.assertTrue(self.old.exists())
        self.assertFalse(self.new.exists())
        self.assertNotIn('gtit', engine._state(self.root)['managed'])

    def test_rollback_rejects_changed_file(self):
        engine.apply(self.root, self.manifest, confirm_closed=True,
                     allow_first_install=True, downloader=self.download)
        make_jar(self.new, 'tampered')
        with self.assertRaises(engine.UpdateError):
            engine.rollback(self.root)
        self.assertTrue(self.new.exists())

    def test_bad_hash_download_is_rejected_without_touching_old(self):
        def corrupt(mod, dest):
            dest.write_bytes(b'not-a-jar')
        with self.assertRaises(engine.UpdateError):
            engine.apply(self.root, self.manifest, confirm_closed=True,
                         allow_first_install=True, downloader=corrupt)
        self.assertTrue(self.old.exists())
        self.assertFalse(self.new.exists())

    def test_duplicate_old_mods_block(self):
        make_jar(self.root/'mods'/'gtit-1.1.jar','duplicate')
        with self.assertRaisesRegex(engine.UpdateError,'varios JAR'):
            engine.inspect(self.root, self.manifest)

    def test_first_install_requires_approval(self):
        self.old.unlink()
        with self.assertRaisesRegex(engine.UpdateError,'nuevos addons'):
            engine.apply(self.root,self.manifest,confirm_closed=True,downloader=self.download)

    def test_existing_unmodified_jar_is_adopted(self):
        self.old.unlink()
        import shutil
        shutil.copy2(self.payload, self.new)
        self.assertEqual(engine.inspect(self.root,self.manifest)[0]['action'], 'adoptar')
        engine.apply(self.root,self.manifest,confirm_closed=True,allow_first_install=True)
        self.assertEqual(engine.inspect(self.root,self.manifest)[0]['action'], 'igual')

    def test_no_overwrite_unmanaged_filename(self):
        make_jar(self.new,'somebody')
        with self.assertRaisesRegex(engine.UpdateError,'varios JAR'):
            engine.inspect(self.root, self.manifest)

    def test_manifest_rejects_path_traversal_and_bad_url(self):
        m=json.loads(json.dumps(self.manifest))
        m['mods'][0]['filename'] = '../other.jar'
        with self.assertRaises(engine.UpdateError): engine.validate_manifest(m)
        m=json.loads(json.dumps(self.manifest))
        m['mods'][0]['url'] = 'https://attacker.example/x.jar'
        with self.assertRaises(engine.UpdateError): engine.validate_manifest(m)

    def test_manifest_rejects_server_side(self):
        m=json.loads(json.dumps(self.manifest))
        m['mods'][0]['side'] = 'server'
        with self.assertRaises(engine.UpdateError): engine.validate_manifest(m)

    def test_local_jar_modified_after_adoption_blocks(self):
        engine.apply(self.root, self.manifest, confirm_closed=True,
                     allow_first_install=True, downloader=self.download)
        make_jar(self.new, 'tamper')
        with self.assertRaises(engine.UpdateError): engine.inspect(self.root, self.manifest)

    def test_running_game_blocks_apply(self):
        with mock.patch.object(engine, 'game_running', return_value=['42']):
            with self.assertRaises(engine.UpdateError):
                engine.apply(self.root, self.manifest, confirm_closed=True,
                             allow_first_install=True, downloader=self.download)

    def test_pending_transaction_can_be_recovered(self):
        engine.apply(self.root, self.manifest, confirm_closed=True,
                     allow_first_install=True, downloader=self.download)
        backup = next((self.root / engine.STATE_SUBDIR / 'backups').iterdir())
        tx = engine._read_json(backup / 'transaction.json', {})
        tx['status'] = 'pending'
        engine._write_json(backup / 'transaction.json', tx)
        engine._write_json(self.root / engine.STATE_SUBDIR / 'pending.json', {'transaction':backup.name})
        self.assertEqual(engine.rollback(self.root), backup.name)
        self.assertTrue(self.old.exists())

    def test_installed_other_mod_not_touched(self):
        additional = self.root / 'mods' / 'SomeOtherMod.jar'
        make_jar(additional, 'unrelated')
        initial = engine.sha256(additional)
        engine.apply(self.root,self.manifest,confirm_closed=True,allow_first_install=True,downloader=self.download)
        self.assertEqual(engine.sha256(additional), initial)

    def test_lockfile_blocks_parallel_update(self):
        with engine._lock(self.root):
            with self.assertRaises(engine.UpdateError):
                engine.apply(self.root,self.manifest,confirm_closed=True,allow_first_install=True,downloader=self.download)

if __name__=='__main__': unittest.main()

class AdditionalGuards(unittest.TestCase):
    def setUp(self):
        self.fixture = ClientTests('test_detect_initial_update_and_preserve_settings')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown if hasattr(self.fixture,'tearDown') else lambda: None)
        self.addCleanup(self.fixture.doCleanups)

    def test_pack_id_mismatch_is_blocked(self):
        f=self.fixture
        engine.apply(f.root, f.manifest, confirm_closed=True, allow_first_install=True, downloader=f.download)
        bad=json.loads(json.dumps(f.manifest))
        bad['pack_id']='other-server-addons'
        with self.assertRaisesRegex(engine.UpdateError,'otro pack_id'):
            engine.inspect(f.root,bad)

    def test_corrupt_backup_rejected_before_mutation(self):
        f=self.fixture
        engine.apply(f.root, f.manifest, confirm_closed=True, allow_first_install=True, downloader=f.download)
        backup=next((f.root/engine.STATE_SUBDIR/'backups').iterdir())
        (backup/'files'/f.old.name).write_bytes(b'tamper')
        with self.assertRaisesRegex(engine.UpdateError,'Backup corrupto'):
            engine.rollback(f.root)
        self.assertTrue(f.new.exists())
        self.assertFalse(f.old.exists())

class ConfigResetClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'minecraft'
        (self.root/'mods').mkdir(parents=True)
        (self.root/'config'/'GTNotLeisure').mkdir(parents=True)
        (self.root/'config'/'GregTech').mkdir(parents=True)
        (self.root/'config'/'GTNotLeisure'/'general.cfg').write_text('user=12')
        (self.root/'config'/'GTNotLeisure'/'client.cfg').write_text('ui=true')
        (self.root/'config'/'GregTech'/'GregTech.lang').write_text('personalized')
        self.old=self.root/'mods'/'sciencenotleisure-0.1.jar'
        self.new=self.root/'mods'/'sciencenotleisure-0.2.jar'
        make_jar(self.old,'old')
        self.official=Path(self.temp.name)/'official.jar'
        make_jar(self.official,'new')
        self.manifest={
            'schema_version':1,'pack_id':'private-mods','gtnh_version':'2.9.0-final','mods':[{ 
                'id':'gtnl','repo':'GTNotLeisure/GT-Not-Leisure','name':'GT Not Leisure','side':'both',
                'version':'v0.2','filename':self.new.name,'sha256':engine.sha256(self.official),
                'url':'https://github.com/GTNotLeisure/GT-Not-Leisure/releases/download/v0.2/sciencenotleisure-0.2.jar',
                'installed_regex':r'(?i)^sciencenotleisure[-_.].*\.jar$',
                'reset_on_update':['config/GTNotLeisure/general.cfg','config/GregTech/GregTech.lang']
            }]}

    def test_approval_backup_and_rollback_of_configs(self):
        changes=engine.inspect(self.root,self.manifest)
        self.assertEqual(len(changes[0]['config_resets']),2)
        with self.assertRaisesRegex(engine.UpdateError,'aprobacion'):
            engine.apply(self.root,self.manifest,confirm_closed=True,allow_first_install=True)
        import shutil
        tx=engine.apply(self.root,self.manifest,confirm_closed=True,allow_first_install=True,
                        approve_config_reset=True,expected_changes=changes,
                        downloader=lambda mod,dst: shutil.copy2(self.official,dst))
        self.assertFalse((self.root/'config'/'GTNotLeisure'/'general.cfg').exists())
        self.assertFalse((self.root/'config'/'GregTech'/'GregTech.lang').exists())
        self.assertEqual((self.root/'config'/'GTNotLeisure'/'client.cfg').read_text(),'ui=true')
        engine.rollback(self.root,tx)
        self.assertEqual((self.root/'config'/'GTNotLeisure'/'general.cfg').read_text(),'user=12')
        self.assertEqual((self.root/'config'/'GregTech'/'GregTech.lang').read_text(),'personalized')
        self.assertTrue(self.old.is_file())

    def test_manifest_cannot_reset_an_arbitrary_file(self):
        data=json.loads(json.dumps(self.manifest))
        data['mods'][0]['reset_on_update'].append('options.txt')
        with self.assertRaises(engine.UpdateError): engine.validate_manifest(data)

    def test_manual_review_required_after_config_regeneration(self):
        import shutil
        tx=engine.apply(self.root,self.manifest,confirm_closed=True,allow_first_install=True,
                        approve_config_reset=True,downloader=lambda mod,dst: shutil.copy2(self.official,dst))
        (self.root/'config'/'GTNotLeisure'/'general.cfg').write_text('generated by updated mod')
        with self.assertRaisesRegex(engine.UpdateError,'Config regenerado'):
            engine.rollback(self.root,tx)
