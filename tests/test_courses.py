import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kippy.storage import Store
from kippy.documents import upload
from kippy.web import App
from test_kippy import FakeClient
import test_kippy as fixtures


class CourseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
    def tearDown(self): self.temp.cleanup()
    def add(self, name='Differential Equations'): return self.store.create_course({'name': name, 'code': 'MATH246', 'description': 'Review equations'})
    def test_fresh_install_has_no_courses_chats_or_example_files(self):
        self.assertEqual(self.store.courses(), [])
        self.assertEqual(self.store.conversations(), [])
        self.assertEqual(self.store.materials(), [])
        self.assertEqual(self.store.legacy(), {'conversations': [], 'materials': []})
    def test_create_rename_and_persist_one_conversation(self):
        c=self.add(); key=c['conversation']['id']
        updated=self.store.update_course(c['id'], {'name':'Advanced ODEs', 'code':'MATH 246'})
        self.assertEqual(updated['conversation']['id'],key)
        self.assertEqual(updated['name'],'Advanced ODEs')
        restored=Store(self.temp.name).course(c['id'])
        self.assertEqual(restored['code'],'MATH 246')
        self.assertEqual(len(self.store.conversations()),1)
        with self.store.connect() as db, self.assertRaises(sqlite3.IntegrityError):
            db.execute('INSERT INTO course_conversations VALUES (?,?)',(c['id'],key))
    def test_course_validation_is_atomic_and_names_are_not_hardcoded(self):
        for data in [{}, {'name':' '}, {'name':'x'*121}, {'name':123}, {'name':'OK','code':'x'*41}, {'name':'OK','description':None}]:
            with self.assertRaises(ValueError): self.store.create_course(data)
        self.assertEqual(self.store.courses(),[])
        c=self.store.create_course({'name':'日本語 & <Logic>'})
        self.assertEqual(c['name'],'日本語 & <Logic>')
    def test_chat_and_all_ready_documents_are_course_scoped(self):
        app=App(self.temp.name,FakeClient());a=app.store.create_course({'name':'Math'});b=app.store.create_course({'name':'Chemistry'})
        da=upload(app.store,'same.txt',b'MATH_ONLY integrating factors',1000,a['id'])
        db=upload(app.store,'same.txt',b'CHEM_ONLY atomic orbitals',1000,b['id'])
        app.chat({'course_id':a['id'],'request_id':'a'*32,'message':'Summarize my lectures','material_ids':[db['id']]})
        first=str(app.client.calls[-1]);self.assertIn('MATH_ONLY',first);self.assertNotIn('CHEM_ONLY',first)
        result=app.chat({'course_id':b['id'],'request_id':'a'*32,'message':'Summarize my lectures','conversation_id':a['conversation']['id'],'material_ids':[da['id']]})
        second=str(app.client.calls[-1]);self.assertIn('CHEM_ONLY',second);self.assertNotIn('MATH_ONLY',second)
        self.assertIn('Chemistry',second);self.assertEqual({s['material_id'] for s in result['sources']},{db['id']})
        self.assertEqual(len(app.store.course(a['id'])['conversation']['messages']),2)
        self.assertEqual(len(app.store.course(b['id'])['conversation']['messages']),2)
        app.chat({'course_id':b['id'],'request_id':'b'*32,'message':'Explain that again'})
        self.assertNotIn('MATH_ONLY',str(app.client.calls[-1]))
    def test_failed_files_stay_in_their_course_but_not_retrieval(self):
        a=self.add();b=self.add('Chemistry')
        f=upload(self.store,'broken.pdf',b'not a pdf',1000,a['id'])
        self.assertEqual(f['status'],'failed')
        self.assertEqual(self.store.course(a['id'])['materials'][0]['id'],f['id'])
        self.assertEqual(self.store.course_chunks(a['id']),[])
        self.assertEqual(self.store.course(b['id'])['materials'],[])
        with self.assertRaises(LookupError): self.store.delete_course_material(b['id'],f['id'])
    def test_delete_course_cascades_only_its_files_history_and_turns(self):
        a=self.add();b=self.add('Chemistry')
        da=upload(self.store,'a.txt',b'Alpha',100,a['id']);db=upload(self.store,'b.txt',b'Beta',100,b['id'])
        self.store.begin_turn(a['conversation']['id'],'a'*32,'Explain')
        self.store.finish_turn(a['conversation']['id'],'a'*32,'Answer',[])
        self.store.delete_course(a['id'])
        with self.assertRaises(LookupError): self.store.course(a['id'])
        with self.assertRaises(LookupError): self.store.conversation(a['conversation']['id'])
        self.assertFalse((self.store.uploads/da['id']).exists())
        self.assertEqual((self.store.uploads/db['id']).read_bytes(),b'Beta')
        self.assertEqual(self.store.course_chunks(a['id']),[])
        with self.store.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM turns').fetchone()[0],0)
            for table,column,value in [('materials','id',da['id']),('chunks','material_id',da['id']),('messages','conversation_id',a['conversation']['id']),('course_materials','course_id',a['id']),('course_conversations','course_id',a['id'])]:
                self.assertEqual(con.execute(f'SELECT count(*) FROM {table} WHERE {column}=?',(value,)).fetchone()[0],0)
            self.assertEqual(con.execute('SELECT count(*) FROM materials WHERE id=?',(db['id'],)).fetchone()[0],1)
        self.assertEqual(self.store.course(b['id'])['conversation']['id'],b['conversation']['id'])
        self.assertEqual([c['id'] for c in Store(self.temp.name).courses()],[b['id']])
    def test_delete_file_failure_keeps_course_and_restores_moved_files(self):
        c=self.add();a=upload(self.store,'a.txt',b'Alpha',100,c['id']);b=upload(self.store,'b.txt',b'Beta',100,c['id'])
        original=Path.replace
        def fail(path,target):
            if path==self.store.uploads/a['id']: raise OSError('Cannot move file')
            return original(path,target)
        with patch.object(Path,'replace',fail), self.assertRaises(OSError):self.store.delete_course(c['id'])
        self.assertEqual(len(self.store.course(c['id'])['materials']),2)
        self.assertEqual((self.store.uploads/a['id']).read_bytes(),b'Alpha')
        self.assertEqual((self.store.uploads/b['id']).read_bytes(),b'Beta')
    def test_restart_recovers_interrupted_file_deletion(self):
        c=self.add();m=upload(self.store,'x.txt',b'Keep original',100,c['id'])
        trash=self.store.uploads/('.deleting-'+'a'*32);trash.mkdir()
        (self.store.uploads/m['id']).replace(trash/m['id'])
        restored=Store(self.temp.name)
        self.assertEqual((restored.uploads/m['id']).read_bytes(),b'Keep original')
        self.assertFalse(trash.exists())
        trash.mkdir();(restored.uploads/m['id']).replace(trash/m['id'])
        with restored.connect() as db:db.execute('DELETE FROM materials WHERE id=?',(m['id'],))
        Store(self.temp.name)
        self.assertFalse(trash.exists())
    def legacy_fixture(self):
        # Produce exactly the original six-table schema, with real prior content.
        m=upload(self.store,'shared.txt',b'Original shared equations',1000)
        threads=[]
        for label in ['Old Math','Old Physics']:
            c=self.store.create_conversation();self.store.select(c['id'],[m['id']]);self.store.begin_turn(c['id'],'a'*32,label)
            self.store.finish_turn(c['id'],'a'*32,'Saved answer',[{'material_id':m['id'],'name':m['name'],'location':'lines 1–1'}]);threads.append(c['id'])
        with self.store.connect() as db:
            for table in ['legacy_imports','course_materials','course_conversations','courses']:db.execute('DROP TABLE '+table)
        return m, threads
    def test_existing_schema_migration_does_not_automatically_create_courses(self):
        m,threads=self.legacy_fixture();self.store=Store(self.temp.name)
        self.assertEqual(self.store.courses(),[])
        self.assertEqual(len(self.store.legacy()['conversations']),2)
        self.assertEqual(len(self.store.conversation(threads[0])['messages']),2)
        self.assertEqual((self.store.uploads/m['id']).read_bytes(),b'Original shared equations')
        # Original insert shape still works, important for deployment rollback.
        self.store.create_conversation()
    def test_import_shared_legacy_files_keeps_originals_and_isolates_copies(self):
        m,threads=self.legacy_fixture();self.store=Store(self.temp.name)
        a=self.store.create_course({'name':'Math','legacy_conversation_id':threads[0]})
        b=self.store.create_course({'name':'Physics','legacy_conversation_id':threads[1]})
        aid,bid=a['materials'][0]['id'],b['materials'][0]['id']
        self.assertEqual(len({aid,bid,m['id']}),3)
        self.assertEqual(a['conversation']['messages'][1]['sources'][0]['material_id'],aid)
        self.assertEqual(self.store.conversation(threads[0])['messages'][1]['sources'][0]['material_id'],m['id'])
        with self.assertRaises(ValueError):self.store.create_course({'name':'Duplicate','legacy_conversation_id':threads[0]})
        self.store.delete_course(a['id'])
        self.assertTrue((self.store.uploads/m['id']).exists());self.assertTrue((self.store.uploads/bid).exists())
        self.assertEqual(len(self.store.course(b['id'])['conversation']['messages']),2)
    def test_loose_legacy_file_import_and_copy_failure_roll_back(self):
        m=upload(self.store,'notes.txt',b'Persist this',1000)
        before=set(self.store.uploads.iterdir())
        with patch('kippy.storage.shutil.copyfile',side_effect=OSError('Disk full')),self.assertRaises(OSError):
            self.store.create_course({'name':'Math','legacy_material_ids':[m['id']]})
        self.assertEqual(self.store.courses(),[]);self.assertEqual(set(self.store.uploads.iterdir()),before)
        c=self.store.create_course({'name':'Math','legacy_material_ids':[m['id']]})
        self.assertEqual(self.store.course_chunks(c['id'])[0]['text'],'Persist this')
        self.assertEqual(Store(self.temp.name).course(c['id'])['materials'],c['materials'])
    def test_upload_to_unknown_course_never_writes_file(self):
        with self.assertRaises(LookupError):upload(self.store,'x.txt',b'Text',100,'f'*32)
        self.assertEqual(list(self.store.uploads.iterdir()),[])


class CourseHTTPTests(unittest.TestCase):
    setUp=fixtures.HTTPTests.setUp
    tearDown=fixtures.HTTPTests.tearDown
    request=fixtures.HTTPTests.request
    def course(self,name):return self.request('/api/courses',{'name':name})[1]
    def test_fresh_course_create_rename_and_explicit_delete_confirmation(self):
        self.assertEqual(self.request('/api/courses')[1]['courses'],[])
        a=self.course('Math');id=a['id']
        self.assertEqual(self.request('/api/courses/'+id,{'name':'ODEs'})[1]['name'],'ODEs')
        self.assertEqual(self.request('/api/courses/'+id,{},'DELETE')[0],400)
        self.assertEqual(self.request('/api/courses/'+id,{'confirm_course_id':'f'*32},'DELETE')[0],400)
        self.assertEqual(self.request('/api/courses/'+id,{'confirm_course_id':id},'DELETE')[0],200)
        self.assertEqual(self.request('/api/courses/'+id)[0],404)
    def test_foreign_course_material_deletion_is_rejected_and_global_routes_removed(self):
        a=self.course('Math');b=self.course('Chemistry')
        _,m=self.request(f"/api/courses/{a['id']}/materials?filename=a.txt",b'integrating factors',headers={'Content-Type':'application/octet-stream'})
        self.assertEqual(self.request(f"/api/courses/{b['id']}/materials/{m['id']}",method='DELETE')[0],404)
        self.assertEqual(self.request('/api/courses/'+a['id'])[1]['materials'][0]['id'],m['id'])
        self.assertEqual(self.request('/api/chat',{'course_id':'f'*32,'message':'Explain','request_id':'a'*32})[0],404)
        self.assertEqual(self.request('/api/chat',{'conversation_id':a['conversation']['id'],'message':'Explain','request_id':'a'*32})[0],400)
        for path in ['/api/materials','/api/conversations']:
            self.assertEqual(self.request(path)[0],404)
            self.assertEqual(self.request(path,{'name':'x'})[0],404)
    def test_empty_course_asks_for_documents_and_never_uses_other_courses_files(self):
        a=self.course('Math');b=self.course('Chemistry')
        upload(self.server.app.store,'a.txt',b'SECRET_MATH equations',100,a['id'])
        code,result=self.request('/api/chat',{'course_id':b['id'],'request_id':'a'*32,'message':'Summarize my lecture'})
        self.assertEqual(code,200);self.assertIn('upload',result['content']);self.assertEqual(self.client.calls,[])
    def test_legacy_discovery_and_import_do_not_reassign_course_owned_files(self):
        old=self.server.app.store.create_conversation();m=upload(self.server.app.store,'old.txt',b'Original',100)
        self.assertEqual(self.request('/api/legacy')[1]['conversations'][0]['id'],old['id'])
        c=self.request('/api/courses',{'name':'Recovered','legacy_conversation_id':old['id'],'legacy_material_ids':[m['id']]})[1]
        mid=c['materials'][0]['id']
        self.assertEqual(self.request('/api/courses',{'name':'Wrong','legacy_material_ids':[mid]})[0],400)
        self.assertEqual(len(self.request('/api/courses')[1]['courses']),1)
