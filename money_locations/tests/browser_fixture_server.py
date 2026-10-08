"""Browser test server with synthetic LifeStage, never a live provider login."""
from pathlib import Path
import os
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from server import create_server
from moneyhub import Moneyhub
from test_moneyhub import FakeClient

class BrowserClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.connected=False
    def login(self,email,password):
        if email!='example@example.test' or password!='fixture-password':
            raise ValueError('Fixture login rejected.')
        self.login_token='fixture-challenge'
        self.challenge_until=time.time()+600
        return {'needs_code':True}
    def verify(self,code):
        if code!='123456':
            raise ValueError('Fixture code rejected.')
        self.connected=True
        self.login_token=None
        return {'needs_code':False}

if __name__=='__main__':
    path=Path(os.environ['MONEY_DATA'])/'money.sqlite'
    client=BrowserClient()
    hub=Moneyhub(path,client,saved_email="example@example.test",saved_password="fixture-password")
    server=create_server(path,host='127.0.0.1',port=int(os.environ['PORT']),local=True,password='browser-fixture',moneyhub=hub)
    print('Fixture server ready',flush=True)
    server.serve_forever()
