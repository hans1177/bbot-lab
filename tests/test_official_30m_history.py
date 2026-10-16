# 파일명: tests/test_official_30m_history.py
import ast,hashlib,io,json,os,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
import yaml

REPO=Path(__file__).resolve().parents[1]
STEPS={s.get('name'):s for s in yaml.safe_load((REPO/'.github/workflows/research-run.yml').read_text())['jobs']['research']['steps'] if s.get('name')}
CODE=STEPS['Prepare official long 30M history']['run']
class OfficialThirtyMinuteTests(unittest.TestCase):
    def setUp(self):
        tree=ast.parse(CODE)
        functions=ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef)],type_ignores=[])
        import re,urllib.error,urllib.request,time
        self.ns={'np':np,'pd':pd,'hashlib':hashlib,'io':io,'zipfile':zipfile,'re':re,'COLUMNS':['open_time','open','high','low','close','volume'],'urllib':__import__('urllib'),'time':time}
        exec(compile(functions,'official-data-functions','exec'),self.ns)
    def archive(self,header=False,bad_time=False):
        start=1577836800000
        rows=[]
        for i in range(3):
            ts=start+i*1800000+(1 if bad_time else 0)
            rows.append(f'{ts},100,102,99,101,12,{ts+1799999},0,1,0,0,0')
        if header:rows.insert(0,'open_time,open,high,low,close,volume,close_time,quote_volume,trades,taker_base,taker_quote,ignore')
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as z:z.writestr('BTCUSDT-30m-2020-01.csv','\n'.join(rows))
        data=raw.getvalue();checksum=(hashlib.sha256(data).hexdigest()+'  BTCUSDT-30m-2020-01.zip').encode()
        return data,checksum
    def test_official_header_and_headerless_formats(self):
        for header in (True,False):
            data,checksum=self.archive(header)
            frame,_=self.ns['parse_archive'](data,checksum,'BTCUSDT','2020-01')
            self.assertEqual(len(frame),3);self.assertEqual(frame.open_time.dtype,np.dtype('int64'))
    def test_checksum_mismatch_rejected(self):
        data,checksum=self.archive()
        with self.assertRaisesRegex(RuntimeError,'CHECKSUM'):self.ns['parse_archive'](data+b'x',checksum,'BTCUSDT','2020-01')
    def test_wrong_time_grid_rejected(self):
        data,checksum=self.archive(bad_time=True)
        with self.assertRaisesRegex(RuntimeError,'TIME_GRID'):self.ns['parse_archive'](data,checksum,'BTCUSDT','2020-01')
    def test_404_is_absent_but_restriction_is_error(self):
        import urllib.error,urllib.request
        for code in (404,451):
            with patch.object(urllib.request,'urlopen',side_effect=urllib.error.HTTPError('url',code,'fixture',None,None)):
                if code==404:self.assertIsNone(self.ns['download']('https://example.test',100))
                else:
                    with self.assertRaises(urllib.error.HTTPError):self.ns['download']('https://example.test',100)
    def test_history_precedes_restore_research_and_live_feedback(self):
        names=list(STEPS)
        self.assertLess(names.index('Prepare official long 30M history'),names.index('Restore exact research state'))
        self.assertLess(names.index('Run existing research controller'),names.index('Read fresh server market feedback'))
        self.assertIn("'30M','1H','6H','12H','1D','1W'",CODE)
