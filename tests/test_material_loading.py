# 파일명: tests/test_material_loading.py
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import pandas as pd

p=Path(__file__).resolve().parents[1]/'strategy_lab/run_strategy.py'
spec=importlib.util.spec_from_file_location('material_loading_runner',p)
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)

class MaterialLoadingTests(unittest.TestCase):
    def test_ohlcv_does_not_import_live_materials(self):
        frame=pd.DataFrame({'close':[1,2]})
        with patch.object(r,'load_raw_frame',return_value=frame),patch.object(r,'load_material_modules',side_effect=AssertionError('unneeded import')):
            self.assertIs(r.source_frame_for_symbol(None,None,{},'BTCUSDT','1H','OHLCV',{}),frame)
    def test_monthly_materials_still_required_and_cached(self):
        modules={};monthly={};frame=pd.DataFrame({'ts':[1]})
        with patch.object(r,'load_material_modules',return_value={'macd':object()}) as load,patch.object(r,'build_monthly_material_frame',return_value=frame):
            r.source_frame_for_symbol(None,None,modules,'BTCUSDT','1M','CACHE_MATERIALS',monthly)
            r.source_frame_for_symbol(None,None,modules,'BTCUSDT','1M','CACHE_MATERIALS',monthly)
            self.assertEqual(load.call_count,1)
            self.assertIn('macd',modules)
    def test_missing_monthly_materials_fail_closed(self):
        with patch.object(r,'load_material_modules',side_effect=FileNotFoundError('missing material')):
            with self.assertRaises(FileNotFoundError):
                r.source_frame_for_symbol(None,None,{},'BTCUSDT','1M','CACHE_MATERIALS',{})
