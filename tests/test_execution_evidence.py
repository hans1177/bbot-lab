# 파일명: test_execution_evidence.py
import ast,json,sys,tempfile,unittest,logging
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]/'runtime'
sys.path.insert(0,str(ROOT))
from strategy_leg_ledger import StrategyLegLedger
# 격리 검사에서는 파일 로그를 열지 않는다.
if not logging.getLogger().handlers:
    logging.getLogger().addHandler(logging.NullHandler())

def extract(filename,name):
    node=next(n for n in ast.walk(ast.parse((ROOT/filename).read_text())) if isinstance(n,ast.FunctionDef) and n.name==name)
    ns={'Decimal':Decimal,'time':__import__('time')}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),filename,'exec'),ns)
    return ns[name]

class ExecutionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.ledger=StrategyLegLedger(Path(self.tmp.name)/'fixture.db')
        self.instance=self.ledger.register_instance('FIXTURE','1H','EV_FIXTURE','PV_FIXTURE',tier='CANARY',trailing_policy_version='TP_FIXTURE')
        self.intent=self.ledger.reserve_intent(self.instance,'signal','BTCUSDT','LONG',10,{'leverage':10},100)
        self.ledger.confirm_leverage_for_leg(self.intent['leg_id'],10,10,'BINANCE_SYMBOL_CONFIGURATION')
        self.ledger.mark_submitted(self.intent['intent_id'],'17')
    def ev(self,tid='1',qty=10,cum=10,asset='USDT'):
        return dict(source='BINANCE_ACCOUNT_TRADES',source_event_id='TRADE:'+tid+':17',trade_id=tid,order_id='17',exchange_ts=1792140000,qty_text=str(qty),price_text='100',fee_text='0.1',fee_asset='USDT',realized_pnl_text='0',realized_pnl_asset=asset,cumulative_qty_units=cum)
    def fill(self,tid='1',qty=10,cum=10,asset='USDT'):
        return self.ledger.confirm_fill(self.intent['intent_id'],'TRADE:'+tid+':17',qty,100,'0.1',exchange_order_id='17',fee_asset='USDT',realized_pnl_text='0',economic_evidence=self.ev(tid,qty,cum,asset))
    def rows(self):
        with closing(self.ledger.connect()) as con:
            return [dict(r) for r in con.execute('SELECT * FROM r4_trade_evidence ORDER BY rowid')]
    def test_rest_stub_then_actual_trade_does_not_double_quantity(self):
        self.ledger.confirm_fill(self.intent['intent_id'],'ORDER:17:CUM:10',10,100,exchange_order_id='17')
        self.assertFalse(self.fill())
        self.assertEqual(self.ledger.get_leg(self.intent['leg_id'])['owned_qty_units'],10)
        self.assertEqual(self.rows()[-1]['evidence_complete'],1)
        self.assertFalse(self.fill())
        self.assertEqual(len(self.rows()),2)
    def test_split_actual_trades_enrich_one_stub(self):
        self.ledger.confirm_fill(self.intent['intent_id'],'ORDER:17:CUM:10',10,100,exchange_order_id='17')
        self.assertFalse(self.fill('1',4,4));self.assertFalse(self.fill('2',6,10))
        self.assertEqual(self.ledger.get_leg(self.intent['leg_id'])['owned_qty_units'],10)
        self.assertEqual(sum(r['qty_units'] for r in self.rows() if r['trade_id']),10)
    def test_overlap_different_trade_id_is_rejected(self):
        self.ledger.confirm_fill(self.intent['intent_id'],'ORDER:17:CUM:10',10,100,exchange_order_id='17')
        self.fill('1',6,6)
        with self.assertRaisesRegex(RuntimeError,'INTERVAL_OVERLAP'):
            self.fill('2',6,10)
        self.assertEqual(len(self.rows()),2)
    def test_ws_before_rest_preserves_filled_and_order_identity(self):
        self.fill();self.ledger.mark_submitted(self.intent['intent_id'],'17')
        self.assertEqual(self.ledger.get_intent(self.intent['intent_id'])['status'],'FILLED')
        with self.assertRaisesRegex(RuntimeError,'ORDER_ID_CONFLICT'):
            self.ledger.mark_submitted(self.intent['intent_id'],'18')
    def test_ws_before_submission_receipt(self):
        with self.ledger.transaction() as con:
            con.execute("UPDATE r4_order_intents SET status='INTENT_SAVED' WHERE intent_id=?",(self.intent['intent_id'],))
        self.assertTrue(self.fill())
        self.assertEqual(self.ledger.get_intent(self.intent['intent_id'])['status'],'FILLED')
    def test_out_of_order_ws_without_stub_accounts_both(self):
        self.assertTrue(self.fill('2',6,10));self.assertTrue(self.fill('1',4,4))
        self.assertEqual(self.ledger.get_leg(self.intent['leg_id'])['owned_qty_units'],10)
    def test_missing_asset_can_be_enriched_without_quantity_change(self):
        self.fill(asset='');self.assertEqual(self.rows()[0]['evidence_complete'],0)
        self.assertFalse(self.fill());self.assertEqual(self.rows()[0]['evidence_complete'],1)
        self.assertEqual(self.ledger.get_leg(self.intent['leg_id'])['owned_qty_units'],10)
    def test_conflicting_fee_rolls_back(self):
        self.fill();ev=self.ev();ev['fee_text']='0.2'
        with self.assertRaisesRegex(RuntimeError,'PAYLOAD_CONFLICT'):
            self.ledger.confirm_fill(self.intent['intent_id'],'TRADE:1:17',10,100,economic_evidence=ev)
        self.assertEqual(self.rows()[0]['fee_text'],'0.1')
    def test_legacy_leverage_is_not_verified(self):
        with self.ledger.transaction() as con:
            con.execute('UPDATE r4_active_legs SET policy_snapshot=? WHERE leg_id=?',(json.dumps({'leverage':10}),self.intent['leg_id']))
        self.fill();self.assertEqual(self.rows()[0]['evidence_complete'],0)
        self.assertEqual(self.rows()[0]['capital_basis_usdt'],'')
    def test_exit_stub_then_actual_trade_preserves_closed_quantity(self):
        self.fill();exit=self.ledger.reserve_exit(self.intent['leg_id'],'exit',10,'TRAILING')
        self.ledger.mark_exit_submitted(exit['exit_intent_id'],'19')
        self.ledger.confirm_exit_fill(exit['exit_intent_id'],'ORDER:19:CUM:10',10,110,exchange_order_id='19')
        before=self.ledger.get_leg(self.intent['leg_id'])
        ev=self.ev();ev.update(order_id='19',trade_id='3',source_event_id='TRADE:3:19',price_text='110',realized_pnl_text='100')
        self.assertFalse(self.ledger.confirm_exit_fill(exit['exit_intent_id'],'TRADE:3:19',10,110,'0.1','USDT','100','19',ev))
        self.ledger.mark_exit_submitted(exit['exit_intent_id'],'19')
        self.assertEqual(self.ledger.get_leg(self.intent['leg_id']),before)
    def test_actual_leverage_cannot_be_rewritten_after_fill(self):
        self.fill()
        with self.assertRaisesRegex(RuntimeError,'CANNOT_BE_ADDED_AFTER_FILL'):
            self.ledger.confirm_leverage_for_leg(self.intent['leg_id'],30,30,'BINANCE_SYMBOL_CONFIGURATION')
        self.assertEqual(self.ledger.confirm_leverage_for_leg(self.intent['leg_id'],10,10,'BINANCE_SYMBOL_CONFIGURATION'),10)
    def test_missing_legacy_leverage_cannot_be_backfilled_from_today(self):
        with self.ledger.transaction() as con:
            con.execute('UPDATE r4_active_legs SET policy_snapshot=? WHERE leg_id=?',(json.dumps({'leverage':10}),self.intent['leg_id']))
        self.fill()
        with self.assertRaisesRegex(RuntimeError,'CANNOT_BE_ADDED_AFTER_FILL'):
            self.ledger.confirm_leverage_for_leg(self.intent['leg_id'],10,10,'BINANCE_SYMBOL_CONFIGURATION')

    def closed_cohort(self,stub=False):
        if stub:
            self.ledger.confirm_fill(self.intent['intent_id'],'ORDER:17:CUM:10',10,100,exchange_order_id='17')
        self.fill()
        exit=self.ledger.reserve_exit(self.intent['leg_id'],'exit',10,'TRAILING')
        self.ledger.mark_exit_submitted(exit['exit_intent_id'],'19')
        if stub:
            self.ledger.confirm_exit_fill(exit['exit_intent_id'],'ORDER:19:CUM:10',10,110,exchange_order_id='19')
        ev=self.ev();ev.update(order_id='19',trade_id='3',source_event_id='TRADE:3:19',price_text='110',realized_pnl_text='100')
        self.ledger.confirm_exit_fill(exit['exit_intent_id'],'TRADE:3:19',10,110,'0.1','USDT','100','19',ev)
        start=self.ledger.get_leg(self.intent['leg_id'])['created_ts']
        self.ledger.confirm_funding_coverage(self.intent['leg_id'],'BINANCE_INCOME_HISTORY',min(start,1792140000),max(start,1792140000)+1,'proof',0)
        return self.ledger.performance_tier_cohort(self.instance)['legs'][0]
    def test_normal_actual_trade_cohort_roi(self):
        from r4_performance_tiering import _complete_leg
        result=_complete_leg(self.closed_cohort())
        self.assertAlmostEqual(float(result['net_pnl_usdt']),99.8)
    def test_stub_economic_binding_can_complete_roi(self):
        from r4_performance_tiering import _complete_leg
        result=_complete_leg(self.closed_cohort(stub=True))
        self.assertAlmostEqual(float(result['net_pnl_usdt']),99.8)
    def test_missing_trade_in_stub_cannot_pass_grade(self):
        from r4_performance_tiering import _complete_leg
        item=self.closed_cohort(stub=True)
        item['evidence']=[r for r in item['evidence'] if r['trade_id']!='1']
        with self.assertRaisesRegex(ValueError,'TRADE_PROOF_INCOMPLETE'):
            _complete_leg(item)
    def test_extra_unbound_trade_cannot_pass_grade(self):
        from r4_performance_tiering import _complete_leg
        item=self.closed_cohort();extra=dict(item['evidence'][0]);extra.update(trade_id='999',source_event_id='TRADE:999:17')
        item['evidence'].append(extra)
        with self.assertRaisesRegex(ValueError,'UNBOUND_EXCHANGE_TRADE'):
            _complete_leg(item)
    def test_current_promotion_and_loss_thresholds_preserved(self):
        from r4_performance_tiering import PROMOTION_RULES,LOSS_STREAK_LIMIT
        self.assertEqual(PROMOTION_RULES,{'CANARY':(10,3),'SMALL':(20,5)})
        self.assertEqual(LOSS_STREAK_LIMIT,3)

    def test_terminal_evidence_recovery_never_submits_and_preserves_position(self):
        from multi_strategy_runtime import MultiStrategyRuntime
        self.fill(asset='')
        before=self.ledger.get_leg(self.intent['leg_id'])
        calls=[]
        result=dict(found=True,exchange_order_id='17',cumulative_qty_units=10,fills=[dict(fill_id='TRADE:1:17',qty_units=10,price_ticks=100,fee_text='0.1',fee_asset='USDT',realized_pnl_text='0',economic_evidence=self.ev())])
        exchange=SimpleNamespace(query_by_client_order_id=lambda *a:(calls.append(a) or result))
        runtime=MultiStrategyRuntime.__new__(MultiStrategyRuntime);runtime.ledger=self.ledger;runtime.exchange=exchange
        actions=runtime.recover_pending(evidence_only=True)
        self.assertEqual(actions[0]['action'],'ECONOMIC_EVIDENCE_CHECKED')
        self.assertEqual(self.rows()[0]['evidence_complete'],1)
        self.assertEqual(self.ledger.get_leg(self.intent['leg_id']),before)
        runtime.recover_pending(evidence_only=True)
        self.assertEqual(len(calls),1)
        self.assertEqual(self.ledger.pending_intents(evidence_only=True),[])
    def test_completed_recovery_rejects_order_quantity_conflict(self):
        from multi_strategy_runtime import MultiStrategyRuntime
        self.fill(asset='');before=self.ledger.get_leg(self.intent['leg_id'])
        runtime=MultiStrategyRuntime.__new__(MultiStrategyRuntime);runtime.ledger=self.ledger
        runtime.exchange=SimpleNamespace(query_by_client_order_id=lambda *a:dict(found=True,exchange_order_id='17',cumulative_qty_units=11))
        result=runtime.recover_pending(evidence_only=True)
        self.assertEqual(result[0]['action'],'ECONOMIC_EVIDENCE_HOLD')
        self.assertEqual(self.ledger.get_leg(self.intent['leg_id']),before)
    def test_completed_recovery_does_not_target_legacy_unverified_leverage(self):
        with self.ledger.transaction() as con:
            con.execute('UPDATE r4_active_legs SET policy_snapshot=? WHERE leg_id=?',(json.dumps({'leverage':10}),self.intent['leg_id']))
        self.fill(asset='')
        self.assertEqual(self.ledger.pending_intents(evidence_only=True),[])
    def test_pending_query_default_preserves_original_execution_recovery(self):
        self.assertEqual([r['intent_id'] for r in self.ledger.pending_intents()],[self.intent['intent_id']])
        self.fill(asset='');self.assertEqual(self.ledger.pending_intents(),[])
        self.assertEqual(len(self.ledger.pending_intents(evidence_only=True)),1)
    def test_exact_order_query_does_not_use_recent_order_list(self):
        calls=[]
        dummy=SimpleNamespace(order=SimpleNamespace(client=SimpleNamespace(query_order=lambda **k:(calls.append(k) or dict(symbol='BTCUSDT',clientOrderId='client',orderId=17))),safe_call=lambda f,**k:f(**k)),_normalize_order=lambda symbol,row:dict(exchange_order_id=str(row['orderId'])))
        result=extract('r4_exchange_adapter.py','query_by_client_order_id')(dummy,'client','BTCUSDT')
        self.assertTrue(result['found']);self.assertEqual(calls,[dict(symbol='BTCUSDT',origClientOrderId='client')])
    def test_query_api_failure_does_not_prove_not_submitted(self):
        dummy=SimpleNamespace(order=SimpleNamespace(client=SimpleNamespace(query_order=lambda **k:None),safe_call=lambda f,**k:f(**k)))
        self.assertIsNone(extract('r4_exchange_adapter.py','query_by_client_order_id')(dummy,'client','BTCUSDT')['found'])

    def test_ws_margin_asset_preserved(self):
        captured=[]
        dummy=SimpleNamespace(ledger=SimpleNamespace(get_intent_by_client_order_id=lambda _:dict(self.intent)),exchange=SimpleNamespace(qty_to_units=lambda s,q:int(q),price_to_ticks=lambda s,p:int(p)),process_fill_event=lambda *a:captured.append(a[1]))
        extract('multi_strategy_runtime.py','process_binance_order_trade_update')(dummy,{'e':'ORDER_TRADE_UPDATE','o':{'c':'client','s':'BTCUSDT','l':'10','z':'10','L':'100','n':'0.1','N':'USDT','rp':'0','ma':'USDT','t':1,'i':17,'T':1792140000000}})
        self.assertEqual(captured[0]['economic_evidence']['realized_pnl_asset'],'USDT')
    def normalize(self,trades):
        dummy=SimpleNamespace(order=SimpleNamespace(client=SimpleNamespace(get_account_trades=lambda **k:trades),safe_call=lambda f,**k:f(**k)),qty_to_units=lambda s,q:int(Decimal(q)),units_to_qty=lambda s,q:str(q),price_to_ticks=lambda s,p:int(Decimal(p)))
        return extract('r4_exchange_adapter.py','_normalize_order')(dummy,'BTCUSDT',dict(orderId=17,status='FILLED',executedQty='10',avgPrice='100',side='BUY'))
    def trades(self):
        return [dict(symbol='BTCUSDT',orderId=17,id=1,time=1792140000000,qty='10',price='100',commission='0.1',commissionAsset='USDT',marginAsset='USDT',realizedPnl='0',side='BUY')]
    def test_rest_exact_trade_cost(self):
        row=self.normalize(self.trades())['fills'][0]
        self.assertEqual(row['fill_id'],'TRADE:1:17');self.assertEqual(row['economic_evidence']['fee_text'],'0.1')
    def test_rest_incomplete_response_stays_unverified(self):
        rows=self.trades();rows[0]['qty']='9'
        row=self.normalize(rows)['fills'][0]
        self.assertNotIn('economic_evidence',row)
    def test_rest_wrong_side_not_accepted(self):
        rows=self.trades();rows[0]['side']='SELL'
        self.assertNotIn('economic_evidence',self.normalize(rows)['fills'][0])

if __name__=='__main__':unittest.main()
