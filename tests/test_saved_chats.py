"""Independent conversations, transactional migration, shared sources and queued inference."""
import copy
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from kippy.storage import Store
from kippy.web import App
from kippy.documents import upload
from test_kippy import FakeClient
import test_kippy as fixtures


class SavedChatTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(self.temp.name)
        self.course=self.store.create_course({'name':'Differential Equations','code':'MATH246'})
    def tearDown(self):self.temp.cleanup()
    def turn(self,app,cid,text,rid='a'*32):return app.chat({'course_id':self.course['id'],'conversation_id':cid,'request_id':rid,'message':text})
    def test_many_named_chats_share_files_without_message_or_document_copies(self):
        c=self.course;original=c['conversation']['id'];m=upload(self.store,'variation.txt',b'Variation of parameters solves nonhomogeneous equations using a fundamental solution set.',1000,c['id'])
        a=self.store.create_course_chat(c['id'],'Lecture Review');b=self.store.create_course_chat(c['id'],'Practice Problems')
        self.assertEqual(a['messages'],[]);self.assertEqual(b['messages'],[]);self.assertEqual(a['material_ids'],[m['id']]);self.assertEqual(b['material_ids'],[m['id']])
        self.assertEqual(len(self.store.materials()),1);self.assertEqual(len(list(self.store.uploads.iterdir())),1)
        renamed=self.store.rename_course_chat(c['id'],a['id'],'New chat');self.store.begin_turn(a['id'],'c'*32,'Do not rename my chat')
        self.store.finish_turn(a['id'],'c'*32,'Saved',[])
        self.assertEqual(self.store.conversation(a['id'])['title'],'New chat')
        self.assertEqual(self.store.conversation(original)['messages'],[])
        restored=Store(self.temp.name);self.assertEqual(len(restored.course(c['id'])['conversations']),3);self.assertEqual(restored.conversation(a['id'])['messages'][-1]['content'],'Saved')
        for name in ['',None,[], 'x'*121,'line\nbreak']:
            with self.assertRaises(ValueError):self.store.rename_course_chat(c['id'],a['id'],name)
    def test_creation_retry_is_idempotent_even_after_rename_or_delete(self):
        c=self.course;a=self.store.create_course_chat(c['id'],'One','a'*32)
        self.store.rename_course_chat(c['id'],a['id'],'Renamed')
        retry=self.store.create_course_chat(c['id'],'One','a'*32)
        self.assertEqual(retry['id'],a['id']);self.assertEqual(retry['title'],'Renamed');self.assertEqual(len(self.store.course(c['id'])['conversations']),2)
        self.store.delete_course_chat(c['id'],a['id'])
        with self.assertRaises(ValueError):self.store.create_course_chat(c['id'],'One','a'*32)
        self.assertEqual(len(self.store.course(c['id'])['conversations']),1)
    def test_first_split_is_fresh_and_idempotent_and_layout_toggles_keep_pair(self):
        c=self.course;old=c['conversation']['id'];self.store.begin_turn(old,'a'*32,'Original private discussion');self.store.finish_turn(old,'a'*32,'Original private answer',[])
        before=copy.deepcopy(self.store.conversation(old));split=self.store.split_course(c['id'],'b'*32);l=split['workspace'];pair=(l['left_id'],l['right_id'])
        self.assertEqual(len(split['conversations']),3);self.assertNotIn(old,pair)
        for cid in pair:self.assertEqual(self.store.conversation(cid)['messages'],[])
        self.assertEqual(self.store.conversation(old),before)
        self.store.split_course(c['id'],'b'*32);self.store.split_course(c['id'],'c'*32)
        self.assertEqual(len(self.store.course(c['id'])['conversations']),3)
        self.store.update_layout(c['id'],{'mode':'single','single_id':pair[1]});self.store.update_layout(c['id'],{'mode':'double'})
        self.assertEqual(tuple(self.store.course(c['id'])['workspace'][k] for k in ['left_id','right_id']),pair)
        fresh=self.store.split_course(c['id'],'d'*32,True);self.assertEqual(len(fresh['conversations']),5)
        self.store.split_course(c['id'],'d'*32,True);self.assertEqual(len(self.store.course(c['id'])['conversations']),5)
        self.assertEqual(Store(self.temp.name).course(c['id'])['workspace'],fresh['workspace'])
    def test_context_is_only_current_chat_plus_shared_course_sources(self):
        c=self.course;upload(self.store,'variation.txt',b'VARIATION_SOURCE: variation of parameters uses the Wronskian to solve forced differential equations.',1000,c['id'])
        a=self.store.create_course_chat(c['id'],'Lecture Review');b=self.store.create_course_chat(c['id'],'Practice Problems');client=FakeClient();app=App(self.temp.name,client)
        self.turn(app,a['id'],'A_PRIVATE_MARKER Explain variation of parameters')
        self.turn(app,b['id'],'B_PRIVATE_MARKER Give practice problems on variation of parameters')
        self.turn(app,a['id'],'A_FOLLOWUP explain it again','b'*32)
        pa,pb,pa2=['\n'.join(m['content'] for m in call) for call in client.calls]
        self.assertIn('VARIATION_SOURCE',pa);self.assertIn('VARIATION_SOURCE',pb)
        self.assertNotIn('A_PRIVATE_MARKER',pb);self.assertNotIn('B_PRIVATE_MARKER',pa2);self.assertIn('A_PRIVATE_MARKER',pa2)
        self.assertEqual(len(self.store.conversation(a['id'])['messages']),4);self.assertEqual(len(self.store.conversation(b['id'])['messages']),2)
        self.assertEqual(self.store.conversation(a['id'])['messages'][-1]['sources'][0]['name'],'variation.txt')
        chem=self.store.create_course({'name':'Chemistry'});chemchat=chem['conversation']['id']
        app.chat({'course_id':chem['id'],'conversation_id':chemchat,'request_id':'f'*32,'message':'Explain variation of parameters generally'})
        self.assertNotIn('VARIATION_SOURCE',str(client.calls[-1]));self.assertNotIn('A_PRIVATE_MARKER',str(client.calls[-1]))
        with self.assertRaises(LookupError):app.chat({'course_id':chem['id'],'conversation_id':a['id'],'request_id':'e'*32,'message':'Steal history'})
        with self.assertRaises(LookupError):self.store.rename_course_chat(chem['id'],a['id'],'Wrong')
        with self.assertRaises(LookupError):self.store.delete_course_chat(chem['id'],a['id'])
    def test_no_other_chat_history_is_even_loaded_to_build_prompt(self):
        a=self.course['conversation']['id'];b=self.store.create_course_chat(self.course['id'],'Independent');app=App(self.temp.name,FakeClient());read=app.store.conversation
        def own_only(cid):
            self.assertEqual(cid,b['id']);return read(cid)
        with patch.object(app.store,'conversation',side_effect=own_only):self.turn(app,b['id'],'What did I discuss in another chat?')
        self.assertEqual(self.store.conversation(a)['messages'],[])
    def test_layout_rejects_shared_panel_identity_and_foreign_ids(self):
        c=self.course;split=self.store.split_course(c['id'],'a'*32);left=split['workspace']['left_id'];other=self.store.create_course({'name':'Chemistry'})['conversation']['id']
        for data in [{'right_id':left},{'single_id':other},{'left_id':[]},{'mode':'triple'},{'active_panel':'bottom'}]:
            with self.assertRaises(ValueError):self.store.update_layout(c['id'],data)
        self.assertEqual(self.store.course(c['id'])['workspace'],split['workspace'])
    def test_delete_chat_preserves_shared_materials_others_and_repairs_layout(self):
        c=self.course;m=upload(self.store,'notes.txt',b'Course notes',100,c['id']);split=self.store.split_course(c['id'],'a'*32);l=split['workspace'];self.store.begin_turn(l['left_id'],'b'*32,'Delete only this');self.store.finish_turn(l['left_id'],'b'*32,'Answer',[])
        self.store.delete_course_chat(c['id'],l['left_id']);after=self.store.course(c['id'])
        self.assertEqual(len(after['conversations']),2);self.assertNotEqual(after['workspace']['left_id'],after['workspace']['right_id']);self.assertTrue((self.store.uploads/m['id']).exists())
        with self.store.connect() as db:
            for table in ['messages','turns','course_conversations']:self.assertEqual(db.execute(f'SELECT count(*) FROM {table} WHERE conversation_id=?',(l['left_id'],)).fetchone()[0],0)
        self.store.delete_course(c['id']);self.assertEqual(self.store.conversations(),[]);self.assertFalse((self.store.uploads/m['id']).exists());self.assertEqual(self.store.courses(),[])
    def test_last_chat_can_be_deleted_without_deleting_course_documents(self):
        c=self.course;m=upload(self.store,'notes.txt',b'Keep me',100,c['id']);self.store.delete_course_chat(c['id'],c['conversation']['id']);after=self.store.course(c['id'])
        self.assertEqual(after['conversations'],[]);self.assertIsNone(after['conversation']);self.assertIsNone(after['workspace']['single_id']);self.assertTrue((self.store.uploads/m['id']).exists())
        new=self.store.create_course_chat(c['id'],'Fresh');self.store.update_layout(c['id'],{'single_id':new['id']});self.assertEqual(self.store.course(c['id'])['conversation']['id'],new['id'])
    def test_existing_one_to_one_schema_migrates_once_without_losing_history_or_files(self):
        c=self.course;cid=c['conversation']['id'];m=upload(self.store,'keep.txt',b'Original material',100,c['id']);self.store.begin_turn(cid,'a'*32,'Original question');self.store.finish_turn(cid,'a'*32,'Original answer',[])
        original=self.store.conversation(cid);materials=self.store.course_materials(c['id'])
        with sqlite3.connect(self.store.path) as db:
            db.executescript('DROP TABLE course_layouts; DROP TABLE chat_splits; ALTER TABLE course_conversations RENAME TO newer_links; CREATE TABLE course_conversations(course_id TEXT PRIMARY KEY REFERENCES courses(id) ON DELETE CASCADE,conversation_id TEXT NOT NULL UNIQUE REFERENCES conversations(id) ON DELETE CASCADE); INSERT INTO course_conversations SELECT * FROM newer_links; DROP TABLE newer_links;')
        migrated=Store(self.temp.name);after=migrated.course(c['id']);self.assertEqual(after['conversation']['id'],cid);self.assertEqual(after['conversation']['title'],'Original Chat');self.assertEqual(after['conversation']['messages'],original['messages']);self.assertEqual(after['materials'],materials);self.assertEqual((migrated.uploads/m['id']).read_bytes(),b'Original material')
        migrated.rename_course_chat(c['id'],cid,'My Original');again=Store(self.temp.name);self.assertEqual(again.conversation(cid)['title'],'My Original');self.assertEqual(len(again.course(c['id'])['conversations']),1)
    def test_migration_failure_rolls_back_original_table_and_title(self):
        c=self.course
        with sqlite3.connect(self.store.path) as db:
            db.executescript('DROP TABLE course_layouts; DROP TABLE chat_splits; ALTER TABLE course_conversations RENAME TO newer_links; CREATE TABLE course_conversations(course_id TEXT PRIMARY KEY REFERENCES courses(id),conversation_id TEXT NOT NULL UNIQUE REFERENCES conversations(id)); INSERT INTO course_conversations SELECT * FROM newer_links; DROP TABLE newer_links; CREATE TRIGGER prevent_rename BEFORE UPDATE ON conversations BEGIN SELECT RAISE(ABORT,"test failure"); END;')
        with self.assertRaises(sqlite3.IntegrityError):Store(self.temp.name)
        with sqlite3.connect(self.store.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM course_conversations').fetchone()[0],1);self.assertEqual(db.execute('SELECT title FROM conversations').fetchone()[0],'New Chat')
            db.execute('DROP TRIGGER prevent_rename')
        self.assertEqual(Store(self.temp.name).course(c['id'])['conversation']['title'],'Original Chat')
    def test_two_requests_queue_one_model_and_duplicate_does_not_append_again(self):
        c=self.course;a=c['conversation']['id'];b=self.store.create_course_chat(c['id'],'Second')['id'];entered=threading.Event();release=threading.Event()
        class Slow(FakeClient):
            def chat(self,messages):
                if not self.calls:entered.set();self.assert_release=release.wait(5)
                return super().chat(messages)
        client=Slow();app=App(self.temp.name,client);results={};failures=[]
        def run(cid,text):
            try:results[cid]=self.turn(app,cid,text)
            except Exception as e:failures.append(e)
        ta=threading.Thread(target=run,args=(a,'ONLY_A explain equations'));tb=threading.Thread(target=run,args=(b,'ONLY_B practice questions'))
        ta.start();self.assertTrue(entered.wait(2));tb.start();release.set();ta.join(5);tb.join(5);self.assertFalse(ta.is_alive() or tb.is_alive());self.assertEqual(failures,[])
        self.assertEqual(len(client.calls),2);self.assertNotIn('ONLY_A',str(client.calls[1]));self.assertEqual(self.turn(app,a,'ONLY_A explain equations'),results[a]);self.assertEqual(len(client.calls),2)
        for cid in [a,b]:self.assertEqual(len(self.store.conversation(cid)['messages']),2)


class SavedChatHTTPTests(unittest.TestCase):
    setUp=fixtures.HTTPTests.setUp
    tearDown=fixtures.HTTPTests.tearDown
    request=fixtures.HTTPTests.request
    def test_crud_split_confirmation_and_course_validation(self):
        _,c=self.request('/api/courses',{'name':'Math'});base=f"/api/courses/{c['id']}"
        status,a=self.request(base+'/conversations',{'name':'Lecture Review'});self.assertEqual(status,201)
        self.assertEqual(self.request(base+'/conversations/'+a['id'],{'name':'Practice'})[1]['title'],'Practice')
        self.assertEqual(self.request(base+'/conversations/'+a['id'])[0],200)
        split=self.request(base+'/split',{'request_id':'a'*32})[1];self.assertEqual(len(split['conversations']),4)
        self.assertEqual(self.request(base+'/layout',{'right_id':split['workspace']['left_id']})[0],400)
        self.assertEqual(self.request(base+'/conversations/'+a['id'],{},'DELETE')[0],400)
        self.assertEqual(self.request(base+'/conversations/'+a['id'],{'confirm_conversation_id':a['id']},'DELETE')[0],200)
        self.assertEqual(self.request(base+'/conversations/'+a['id'])[0],404)
        self.assertEqual(self.request('/api/chat',{'course_id':c['id'],'message':'Ambiguous target','request_id':'f'*32})[0],400)
        _,chem=self.request('/api/courses',{'name':'Chemistry'})
        self.assertEqual(self.request('/api/chat',{'course_id':chem['id'],'conversation_id':c['conversation']['id'],'message':'Wrong','request_id':'a'*32})[0],404)
    def test_http_accepts_two_near_simultaneous_chats(self):
        _,c=self.request('/api/courses',{'name':'Math'});base=f"/api/courses/{c['id']}";_,b=self.request(base+'/conversations',{'name':'Second'});out=[]
        def run(cid):out.append(self.request('/api/chat',{'course_id':c['id'],'conversation_id':cid,'message':'Explain equations','request_id':'a'*32}))
        a=threading.Thread(target=run,args=(c['conversation']['id'],));t=threading.Thread(target=run,args=(b['id'],));a.start();t.start();a.join(5);t.join(5);self.assertEqual([r[0] for r in out],[200,200]);self.assertEqual(len(self.client.calls),2)
