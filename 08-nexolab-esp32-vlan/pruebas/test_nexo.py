"""Validación de la pasarela y de controles inválidos, sin ESP32 físicas."""
import io,json,math,sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'emulador'))
from control_http import validate
from panel import gateway

class Request:
    def __init__(self,data=b'',headers=None):
        self.rfile=io.BytesIO(data)
        self.headers={'Content-Length':str(len(data)),'X-Nexo-Control':'1',**(headers or {})}
    def _responder(self,code,kind,body):self.result=(code,kind,body)

class TestControl(unittest.TestCase):
    def test_rejects_invalid_numeric_payloads(self):
        for bad in (math.nan,math.inf,-math.inf,'12',True,None,101):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                validate({'modo':'manual','entradas':[bad,0,0]},True)
    def test_rejects_missing_axes_and_invalid_buttons(self):
        for v in ([0,0],[0,0,.5],[0,0,0,0],[0,0,2]):
            with self.subTest(v=v),self.assertRaises(ValueError):validate({'modo':'manual','entradas':v},True)
    def test_manual_robot_preserves_values(self):
        self.assertEqual(validate({'modo':'manual','entradas':[25,44,-20,0]},False)['entradas'],[25,44,-20,0])
    def test_pause_removes_stale_manual_input(self):
        self.assertEqual(validate({'modo':'pausa','entradas':[100,100,1]},True),{'modo':'pausa'})
    def test_unknown_modes_rejected(self):
        for v in ('stop','shell','manual;shutdown',None):
            with self.subTest(v=v),self.assertRaises(ValueError):validate({'modo':v},True)

class TestGateway(unittest.TestCase):
    def test_physical_mode_blocks_web_controls(self):
        h=Request(b'{"modo":"auto"}')
        with patch.object(gateway,'MODE','fisico'),patch.object(gateway,'fetch') as f:
            gateway.post(h,'/api/control/ctrl-1');self.assertEqual(h.result[0],403);f.assert_not_called()
    def test_only_whitelisted_view_resources(self):
        for path in ('/api/zona/pista/../../etc/passwd','/api/zona/http://example.com/cuadro','/api/zona/nao/secrets'):
            h=Request()
            with patch.object(gateway,'fetch') as f:
                self.assertTrue(gateway.get(h,path));self.assertEqual(h.result[0],404);f.assert_not_called()
    def test_offline_renderer_returns_503_not_old_image(self):
        h=Request()
        with patch.object(gateway,'fetch',side_effect=OSError('offline')):
            gateway.get(h,'/api/zona/pepper/cuadro');self.assertEqual(h.result[0],503)
    def test_manual_control_does_not_forward_user_selected_destination(self):
        h=Request(b'{"modo":"manual","rol":"ctrl-3","entradas":[0,0,0]}')
        with patch.object(gateway,'MODE','software'),patch.object(gateway,'fetch',return_value=('application/json',b'{}')) as f:
            gateway.post(h,'/api/control/ctrl-1')
            self.assertEqual(h.result[0],200)
            self.assertEqual(json.loads(f.call_args.args[1])['rol'],'ctrl-1')
    def test_cross_origin_simple_request_rejected(self):
        h=Request(b'{"modo":"auto"}',{'X-Nexo-Control':''})
        with patch.object(gateway,'MODE','software'):
            gateway.post(h,'/api/control/ctrl-1');self.assertEqual(h.result[0],403)
    def test_non_json_control_and_oversize_rejected(self):
        for h in (Request(b'oops'),Request(b'{}',{'Content-Length':'100000'})):
            with patch.object(gateway,'MODE','software'):
                gateway.post(h,'/api/control/ctrl-1');self.assertEqual(h.result[0],400)
if __name__=='__main__':unittest.main(verbosity=2)
