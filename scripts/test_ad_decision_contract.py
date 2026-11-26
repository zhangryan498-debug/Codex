import unittest
from ad_decision_contract import *
class ContractTests(unittest.TestCase):
 def test_ledger(self):
  self.assertEqual(ledger(1050,210,735,575)['post_ad'],-50)
  self.assertAlmostEqual(ledger(1650,310,1260,900)['break_even_gross'],4.583333333333333)
  self.assertAlmostEqual(ledger(1050000,210000,720000,550000)['break_even_settled'],720/170)
 def test_windows(self):
  self.assertEqual(window(275,50,'complete')['roi'],5.5)
  self.assertAlmostEqual(window(345,85,'partial')['roi'],4.0588235294)
  for state in ('partial','missing','stale','zero'):
   self.assertFalse(window(0,0,state)['decision_eligible'])
  for state in ('missing','stale','zero'): self.assertIsNone(window(0,0,state)['roi'])
 def test_remaining(self):
  self.assertEqual(remaining(235000,210000,0),25000)
  self.assertIsNone(remaining(235000,210000,None))
  self.assertEqual(remaining(100,110,0),0)
 def test_loss_caps(self):
  self.assertEqual(safe_loss_spend(50000,0,4,None,True),50000)
  self.assertEqual(safe_loss_spend(50000,10000,4,None,True),40000)
  self.assertEqual(safe_loss_spend(50000,0,4,2,True),100000)
  self.assertIsNone(safe_loss_spend(50000,None,4,2,True))
  self.assertIsNone(safe_loss_spend(50000,0,4,2,False))
  self.assertIsNone(safe_loss_spend(50000,0,4,4,True))
  self.assertGreater(160000,safe_loss_spend(50000,0,4,None,True))
 def test_organic(self):
  x=organic(1500,12,2400,18)
  self.assertAlmostEqual(x['scale'],7.2); self.assertAlmostEqual(x['efficiency'],-1.2)
  self.assertFalse(x['causal'])
 def test_hard_risk_before_stale(self):
  self.assertEqual(review(dict(hard_risk=True,data_state='stale'))['action'],'pause')
  self.assertEqual(review(dict(product_changed_unverified=True,stock=100,data_state='complete'))['action'],'pause')
 def test_hysteresis(self):
  for stock,resume in ((40,40),(42,None),(42,50)):
   self.assertEqual(review(dict(paused=True,stock=stock,stop_stock=40,resume_stock=resume,restart_checks_verified=True,data_state='complete'))['action'],'pause')
 def test_old_a_missing_marginal(self):
  self.assertEqual(review(dict(data_state='complete',requested_action='increase',average_roi=5.2,budget_utilization=.95))['action'],'hold')
 def test_complete_scale(self):
  x=dict(data_state='complete',requested_action='increase',absolute_increment_cap=12,available_balance=100,balance_fraction=.1,risk_spend_cap=20,proposed_increment=10)
  for k in ('same_basis_verified','budget_constraint_evidence_verified','marginal_evidence_verified','control_limits_verified','cooldown_satisfied','trial_or_scale_approval_verified'): x[k]=True
  self.assertEqual(review(x)['action'],'propose_increase'); self.assertFalse(review(x)['execution_authorized'])
  for k in ('same_basis_verified','budget_constraint_evidence_verified','marginal_evidence_verified','control_limits_verified','cooldown_satisfied','trial_or_scale_approval_verified'):
   y=dict(x); y[k]=False; self.assertEqual(review(y)['action'],'hold')
  x['proposed_increment']=12; self.assertEqual(review(x)['action'],'hold')
 def test_invalid(self):
  for value in (True,-1,float('nan'),float('inf'),'1'):
   with self.assertRaises(ValueError): number(value)
  with self.assertRaises(ValueError): window(1,0,'zero')
  with self.assertRaises(ValueError): safe_loss_spend(50,0,0,None,True)
if __name__=='__main__': unittest.main()
