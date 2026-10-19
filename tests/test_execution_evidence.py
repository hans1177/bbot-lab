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
    def test_tier_leg_capacity_is_order_block_not_loop_exception(self):
        # 파일명: test_execution_evidence.py / 이미 보유 한도에 도달했을 때 주문 없이 안전하게 차단한다.
        from multi_strategy_runtime import MultiStrategyRuntime
        runtime = MultiStrategyRuntime(ledger=self.ledger, exchange=None)
        try:
            response = runtime.enter(
                self.instance, "second-signal", "BTCUSDT", "LONG",
                10, {"leverage": 10}, 100, tier_leg_limit=1,
            )
        finally:
            runtime.close()
        self.assertEqual(response["status"], "ENTRY_BLOCKED")
        self.assertEqual(response["reason"], "TIER_CONCURRENT_LEG_LIMIT:1/1")
        self.assertEqual(len(self.ledger.pending_intents()), 1)

    def test_invalid_tier_limit_still_raises(self):
        # 잘못된 설정은 정당한 한도 차단으로 위장하지 않는다.
        from multi_strategy_runtime import MultiStrategyRuntime
        runtime = MultiStrategyRuntime(ledger=self.ledger, exchange=None)
        try:
            with self.assertRaisesRegex(ValueError, "INVALID_TIER_LEG_LIMIT"):
                runtime.enter(
                    self.instance, "second-signal", "BTCUSDT", "LONG",
                    10, {"leverage": 10}, 100, tier_leg_limit=0,
                )
        finally:
            runtime.close()

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
    def legacy_exit_stub(self):
        # 과거 확정 레버리지 없는 Leg를 재현하며 실제 서버/거래소를 사용하지 않는다.
        with self.ledger.transaction() as con:
            con.execute(
                'UPDATE r4_active_legs SET policy_snapshot=? WHERE leg_id=?',
                (json.dumps({'leverage':10}),self.intent['leg_id']),
            )
        self.fill(asset='USDT')
        exit_intent=self.ledger.reserve_exit(self.intent['leg_id'],'legacy-exit',10,'TRAILING')
        self.ledger.mark_exit_submitted(exit_intent['exit_intent_id'],'19')
        self.ledger.confirm_exit_fill(
            exit_intent['exit_intent_id'],'ORDER:19:CUM:10',10,110,
            exchange_order_id='19',
        )
        return exit_intent

    def test_legacy_exit_stub_eligible_without_leverage_proof_but_entry_still_blocked(self):
        target=self.legacy_exit_stub()
        self.assertEqual(self.ledger.pending_intents(evidence_only=True),[])
        eligible=self.ledger.pending_exit_intents(evidence_only=True)
        self.assertEqual([x['exit_intent_id'] for x in eligible],[target['exit_intent_id']])
        with closing(self.ledger.connect()) as con:
            verified=con.execute(
                "SELECT policy_snapshot FROM r4_active_legs WHERE leg_id=?",
                (self.intent['leg_id'],)
            ).fetchone()[0]
        self.assertNotIn('verified_exchange_leverage_x',verified)

    def test_legacy_exit_evidence_enrichment_does_not_repeat_closed_quantity(self):
        from multi_strategy_runtime import MultiStrategyRuntime
        target=self.legacy_exit_stub()
        before=self.ledger.get_leg(self.intent['leg_id'])
        ev=self.ev(tid='3',qty=10,cum=10)
        ev.update(order_id='19',source_event_id='TRADE:3:19',
                  price_text='110',realized_pnl_text='100')
        result=dict(found=True,exchange_order_id='19',cumulative_qty_units=10,
                    fills=[dict(fill_id='TRADE:3:19',qty_units=10,price_ticks=110,
                                fee_text='0.1',fee_asset='USDT',
                                realized_pnl_text='100',economic_evidence=ev)])
        calls=[]
        exchange=SimpleNamespace(query_by_client_order_id=lambda *args:(calls.append(args) or result))
        runtime=MultiStrategyRuntime.__new__(MultiStrategyRuntime)
        runtime.ledger=self.ledger
        runtime.exchange=exchange
        actions=runtime.recover_pending(evidence_only=True)
        self.assertEqual([x['action'] for x in actions],['ECONOMIC_EVIDENCE_CHECKED'])
        self.assertEqual(self.ledger.get_leg(self.intent['leg_id']),before)
        with closing(self.ledger.connect()) as con:
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM r4_leg_events WHERE event_type='EXIT_FILL'").fetchone()[0],
                1,
            )
            good=con.execute(
                "SELECT COUNT(*) FROM r4_trade_evidence WHERE kind='EXIT_FILL' AND evidence_complete=1 AND trade_id='3'"
            ).fetchone()[0]
            self.assertEqual(good,1)
            entry_complete=con.execute(
                "SELECT COUNT(*) FROM r4_trade_evidence WHERE kind='ENTRY_FILL' AND evidence_complete=1"
            ).fetchone()[0]
            self.assertEqual(entry_complete,0)
        self.assertEqual(self.ledger.pending_exit_intents(evidence_only=True),[])
        self.assertEqual(self.ledger.pending_intents(evidence_only=True),[])
        self.assertEqual(len(calls),1)
        self.assertEqual(target['status'],'INTENT_SAVED')

    def test_legacy_exit_four_exchange_trades_bind_without_double_close(self):
        from multi_strategy_runtime import MultiStrategyRuntime
        self.legacy_exit_stub()
        before=self.ledger.get_leg(self.intent['leg_id'])
        fills=[]
        cumulative=0
        for trade_id,amount in [('31',2),('32',3),('33',1),('34',4)]:
            cumulative+=amount
            ev=self.ev(tid=trade_id,qty=amount,cum=cumulative)
            ev.update(order_id='19',source_event_id='TRADE:'+trade_id+':19',
                      price_text='110',realized_pnl_text='0')
            fills.append(dict(fill_id='TRADE:'+trade_id+':19',
                              qty_units=amount,price_ticks=110,fee_text='0.1',
                              fee_asset='USDT',realized_pnl_text='0',
                              economic_evidence=ev))
        exchange=SimpleNamespace(query_by_client_order_id=lambda *args:
            dict(found=True,exchange_order_id='19',cumulative_qty_units=10,fills=fills))
        runtime=MultiStrategyRuntime.__new__(MultiStrategyRuntime)
        runtime.ledger=self.ledger
        runtime.exchange=exchange
        result=runtime.recover_pending(evidence_only=True)
        self.assertEqual([x['action'] for x in result],['ECONOMIC_EVIDENCE_CHECKED'])
        self.assertEqual(self.ledger.get_leg(self.intent['leg_id']),before)
        with closing(self.ledger.connect()) as con:
            result=con.execute(
                "SELECT COUNT(*),COALESCE(SUM(qty_units),0) FROM r4_trade_evidence "
                "WHERE kind='EXIT_FILL' AND evidence_complete=1"
            ).fetchone()
            self.assertEqual(tuple(result),(4,10))
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM r4_leg_events WHERE event_type='EXIT_FILL'"
            ).fetchone()[0],1)
        self.assertEqual(self.ledger.pending_exit_intents(evidence_only=True),[])

    def test_legacy_exit_recovery_rejects_exchange_quantity_conflict(self):
        from multi_strategy_runtime import MultiStrategyRuntime
        self.legacy_exit_stub()
        before=self.ledger.get_leg(self.intent['leg_id'])
        rows_before=self.rows()
        runtime=MultiStrategyRuntime.__new__(MultiStrategyRuntime)
        runtime.ledger=self.ledger
        runtime.exchange=SimpleNamespace(
            query_by_client_order_id=lambda *args:
                dict(found=True,exchange_order_id='19',cumulative_qty_units=11)
        )
        actions=runtime.recover_pending(evidence_only=True)
        self.assertEqual([a['action'] for a in actions],['ECONOMIC_EVIDENCE_HOLD'])
        self.assertEqual(self.ledger.get_leg(self.intent['leg_id']),before)
        self.assertEqual(self.rows(),rows_before)
        self.assertEqual(len(self.ledger.pending_exit_intents(evidence_only=True)),1)

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

    def test_boolean_evidence_restores_pass_after_failure(self):
        ids=[]
        for value in (True,False,True):
            ids.append(self.ledger.record_tier_evidence(self.instance,'CHECK',value,{'source':'CURRENT_OBSERVATION'})['id'])
        self.assertEqual(len(set(ids)),3)
        self.assertTrue(self.ledger.latest_tier_evidence(self.instance,'CANARY')['CHECK']['passed'])
    def test_unchanged_boolean_evidence_does_not_grow_database(self):
        for _ in range(100):
            self.ledger.record_tier_evidence(self.instance,'CHECK',True,{'source':'CURRENT_OBSERVATION'})
        with closing(self.ledger.connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM r4_tier_evidence').fetchone()[0],1)
    def test_explicit_old_event_replay_does_not_replace_latest_observation(self):
        self.ledger.record_tier_evidence(self.instance,'CHECK',True,{},'old-event')
        self.ledger.record_tier_evidence(self.instance,'CHECK',False,{},'new-event')
        self.ledger.record_tier_evidence(self.instance,'CHECK',True,{},'old-event')
        self.assertFalse(self.ledger.latest_tier_evidence(self.instance,'CANARY')['CHECK']['passed'])
    def test_conflicting_explicit_evidence_is_rejected(self):
        self.ledger.record_tier_evidence(self.instance,'CHECK',True,{},'event')
        with self.assertRaisesRegex(RuntimeError,'KEY_PAYLOAD_CONFLICT'):
            self.ledger.record_tier_evidence(self.instance,'CHECK',False,{},'event')
    def recovery_fixture(self):
        import time
        from multi_strategy_runtime import MultiStrategyRuntime
        self.fill()
        now=int(time.time());block=now-60
        row=dict(strategy_instance_id=self.instance,exact_strategy_version='EV_FIXTURE',tier='CANARY',entry_blocked=True,rollback_requested_to_local=True,rollback_target='PREVIOUS_VALIDATED_VERSION',tier_reason='SAFETY_EVIDENCE_FAIL',tier_updated_at=block)
        path=Path(self.tmp.name)/'strategy_config.json'
        path.write_text(json.dumps({'r4_runtime':{'strategy_instances':{self.instance:row}},'preserved':'unchanged'}))
        for name,detail in (
            ('NO_CRITICAL_HARD_FAIL',{'source':'BASKET_RECONCILE_MISMATCH'}),
            ('STRATEGY_LEG_LEDGER_RECONCILE_PASS',{'source':'BASKET_RECONCILE','baskets':[['BTCUSDT','LONG']]}),
        ):
            self.ledger.record_tier_evidence(self.instance,name,False,detail)
        with self.ledger.transaction() as con:
            con.execute('UPDATE r4_tier_evidence SET ts=?',(block-1,))
        self.ledger.record_tier_evidence(self.instance,'NO_CRITICAL_HARD_FAIL',True,{'source':'R4_FULL_CYCLE_SAFETY_PASS'})
        self.ledger.record_tier_evidence(self.instance,'STRATEGY_LEG_LEDGER_RECONCILE_PASS',True,{'source':'BASKET_RECONCILE'})
        self.ledger.reconcile('BTCUSDT','LONG',10,'current-snapshot')
        runtime=MultiStrategyRuntime.__new__(MultiStrategyRuntime);runtime.ledger=self.ledger;runtime.entry_admission_enabled=True
        service=SimpleNamespace(runtime=runtime,_reconcile_cycle_pass=True,_reconcile_cycle_started_at=now,_critical_fail_instances_this_cycle=set())
        node=next(n for n in ast.walk(ast.parse((ROOT/'r4_runtime_service.py').read_text())) if isinstance(n,ast.FunctionDef) and n.name=='_recover_transient_leg_management_safety_latch')
        def write_config(cfg):
            path.write_text(json.dumps(cfg))
        ns=dict(json=json,time=time,R4_LIVE_TIERS={'CANARY','SMALL','NORMAL'},STRATEGY_CONFIG_FILE=path,_atomic_strategy_config=write_config,_log=lambda *a,**k:None)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),'r4_runtime_service.py','exec'),ns)
        recover=ns['_recover_transient_leg_management_safety_latch']
        controller={'required_positive_scale_evidence':['NO_CRITICAL_HARD_FAIL','STRATEGY_LEG_LEDGER_RECONCILE_PASS']}
        return service,row,path,recover,controller
    def test_resolved_basket_lock_recovers_without_tier_or_position_change(self):
        service,row,path,recover,controller=self.recovery_fixture()
        before=self.ledger.get_leg(self.intent['leg_id'])
        self.assertTrue(recover(service,self.instance,row,controller))
        cfg=json.loads(path.read_text());fresh=cfg['r4_runtime']['strategy_instances'][self.instance]
        self.assertFalse(fresh['entry_blocked']);self.assertFalse(fresh['rollback_requested_to_local'])
        self.assertEqual(fresh['tier'],'CANARY');self.assertEqual(cfg['preserved'],'unchanged')
        self.assertEqual(self.ledger.get_leg(self.intent['leg_id']),before)
    def test_unverified_current_cycle_cannot_clear_safety_lock(self):
        service,row,path,recover,controller=self.recovery_fixture();service._reconcile_cycle_pass=False
        self.assertFalse(recover(service,self.instance,row,controller))
        self.assertTrue(json.loads(path.read_text())['r4_runtime']['strategy_instances'][self.instance]['entry_blocked'])
    def test_current_cycle_management_failure_keeps_safety_lock(self):
        service,row,path,recover,controller=self.recovery_fixture();service._critical_fail_instances_this_cycle.add(self.instance)
        self.assertFalse(recover(service,self.instance,row,controller))
    def test_unresolved_basket_keeps_safety_lock(self):
        service,row,path,recover,controller=self.recovery_fixture()
        self.ledger.reconcile('BTCUSDT','LONG',11,'mismatch')
        self.assertFalse(recover(service,self.instance,row,controller))
    def test_loss_streak_lock_is_never_cleared_by_reconcile_recovery(self):
        service,row,path,recover,controller=self.recovery_fixture();row['tier_reason']='CANARY_LOSS_STREAK_LIMIT'
        self.assertFalse(recover(service,self.instance,row,controller))
    def test_unrelated_safety_failure_is_not_auto_recovered(self):
        service,row,path,recover,controller=self.recovery_fixture()
        self.ledger.record_tier_evidence(self.instance,'OTHER_SAFETY',False,{'source':'UNKNOWN'})
        with self.ledger.transaction() as con:
            con.execute("UPDATE r4_tier_evidence SET ts=? WHERE evidence_name='OTHER_SAFETY'",(row['tier_updated_at'],))
        self.assertFalse(recover(service,self.instance,row,controller))
    def test_newer_lock_cannot_be_cleared_from_older_observation(self):
        service,row,path,recover,controller=self.recovery_fixture()
        cfg=json.loads(path.read_text());cfg['r4_runtime']['strategy_instances'][self.instance]['tier_updated_at']+=1;path.write_text(json.dumps(cfg))
        self.assertFalse(recover(service,self.instance,row,controller))
    def test_reconcile_proof_older_than_block_is_not_sufficient(self):
        service,row,path,recover,controller=self.recovery_fixture()
        with self.ledger.transaction() as con:
            con.execute("UPDATE r4_tier_evidence SET ts=? WHERE evidence_name='STRATEGY_LEG_LEDGER_RECONCILE_PASS'",(row['tier_updated_at']-1,))
        self.assertFalse(recover(service,self.instance,row,controller))

    def test_shared_basket_uses_total_ledger_quantity_for_safety_recovery(self):
        service,row,path,recover,controller=self.recovery_fixture()
        other=self.ledger.register_instance('OTHER','1H','EV_OTHER','PV_OTHER',tier='CANARY',trailing_policy_version='TP_OTHER')
        intent=self.ledger.reserve_intent(other,'other-signal','BTCUSDT','LONG',4,{'leverage':10},100)
        self.ledger.mark_submitted(intent['intent_id'],'88')
        self.ledger.confirm_fill(intent['intent_id'],'TRADE:other:88',4,100,exchange_order_id='88')
        self.ledger.reconcile('BTCUSDT','LONG',14,'shared-current')
        self.assertTrue(recover(service,self.instance,row,controller))
        self.assertEqual(self.ledger.get_leg(intent['leg_id'])['owned_qty_units'],4)
    def test_stale_basket_snapshot_cannot_release_safety_lock(self):
        service,row,path,recover,controller=self.recovery_fixture()
        with self.ledger.transaction() as con:
            con.execute('UPDATE r4_basket_snapshots SET updated_ts=0')
        self.assertFalse(recover(service,self.instance,row,controller))
    def test_wrong_failure_source_cannot_release_safety_lock(self):
        service,row,path,recover,controller=self.recovery_fixture()
        with self.ledger.transaction() as con:
            con.execute("UPDATE r4_tier_evidence SET detail_json=? WHERE evidence_name='NO_CRITICAL_HARD_FAIL' AND passed=0",(json.dumps({'source':'UNKNOWN'}),))
        self.assertFalse(recover(service,self.instance,row,controller))

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
    def test_r4_invalid_signal_side_is_blocked_before_entry_plan(self):
        # 파일명: test_execution_evidence.py / 실제 cycle의 검증식을 실행, 주문 경로보다 앞에 있는지 확인한다.
        tree=ast.parse((ROOT/'r4_runtime_service.py').read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='R4RuntimeService')
        cycle=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='cycle')
        guard=next(n for n in ast.walk(cycle) if isinstance(n,ast.If)
                   and isinstance(n.test,ast.Compare)
                   and any(isinstance(op,ast.NotIn) for op in n.test.ops)
                   and 'result.get' in ast.unparse(n.test)
                   and "'side'" in ast.unparse(n.test)
                   and "'BUY'" in ast.unparse(n.test))
        self.assertTrue(any(isinstance(node,ast.Continue) for node in guard.body))
        plan_calls=[n for n in ast.walk(cycle) if isinstance(n,ast.Call)
                    and isinstance(n.func,ast.Attribute) and n.func.attr=='_plan']
        self.assertTrue(plan_calls)
        self.assertLess(guard.lineno,max(n.lineno for n in plan_calls))
        expr=compile(ast.fix_missing_locations(ast.Expression(guard.test)),'guard','eval')
        for side in ('BUY','SELL'):
            self.assertFalse(eval(expr,{},dict(result={'side':side})),side)
        for side in ('','buy','sell','HOLD','UNKNOWN',None):
            self.assertTrue(eval(expr,{},dict(result={'side':side})),side)
        self.assertTrue(eval(expr,{},dict(result={})))

    def test_r4_long_short_live_order_routing_without_network(self):
        # 파일명: test_execution_evidence.py / 실제 주문 전송 없이 LONG/SHORT 매핑을 검증한다.
        # R4 거래소 어댑터의 주문 메서드를 그대로 사용하고 API를 캡처만 한다.
        calls=[]
        def fake_order(**kwargs):
            calls.append(dict(kwargs))
            return {'status':'NEW','executedQty':'0'}
        dummy=SimpleNamespace(
            order=SimpleNamespace(
                client=SimpleNamespace(new_order=fake_order),
                safe_call=lambda fn,**kwargs:fn(**kwargs),
            ),
            units_to_qty=lambda symbol,units:str(units),
            _normalize_order=lambda symbol,row:dict(row),
        )
        submit=extract('r4_exchange_adapter.py','submit')
        reduce=extract('r4_exchange_adapter.py','submit_reduce')
        for position,expected in [('LONG','BUY'),('SHORT','SELL')]:
            intent={'symbol':'BTCUSDT','position_side':position,
                    'requested_qty_units':2,'client_order_id':'test_'+position}
            result=submit(dummy,intent)
            sent=calls.pop()
            self.assertEqual(result['status'],'NEW')
            self.assertEqual(sent['side'],expected)
            self.assertEqual(sent['type'],'MARKET')
            self.assertEqual(sent['quantity'],'2')
            self.assertNotIn('reduceOnly',sent)
            self.assertNotIn('positionSide',sent)
        for position,expected in [('LONG','SELL'),('SHORT','BUY')]:
            leg={'symbol':'BTCUSDT','position_side':position}
            result=reduce(dummy,leg,2,'exit_'+position)
            sent=calls.pop()
            self.assertEqual(result['status'],'NEW')
            self.assertEqual(sent['side'],expected)
            self.assertIs(sent['reduceOnly'],True)
            self.assertEqual(sent['type'],'MARKET')
            self.assertEqual(sent['quantity'],'2')
            self.assertNotIn('positionSide',sent)
        self.assertEqual(calls,[])

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
