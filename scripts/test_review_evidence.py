"""Regression checks for real parser/interpretation failures; no network or credentials."""
import unittest

from collect_review_evidence import normalize_tiktok_detail, redact
from analyze_review_evidence import analyze_product, classify, is_usable


class ReviewEvidenceTests(unittest.TestCase):
    def test_negated_defects_are_not_complaints(self):
        hits={h['keyword'] for h in classify('没有异味，挺好玩的没有别人说的那么硬很软，也不会漏液')}
        self.assertIn('无异味',hits)
        self.assertIn('不漏液',hits)
        self.assertIn('柔软',hits)
        self.assertFalse({'气味明显','偏硬／难捏','破裂／漏液／起皮'} & hits)

    def test_mixed_five_star_review_keeps_both_views(self):
        hits={h['keyword'] for h in classify('虽然被我捏爆了，但是感觉质量不错，主要是我手劲大')}
        self.assertIn('破裂／漏液／起皮',hits)
        self.assertIn('质量认可',hits)
        self.assertNotIn('好玩／解压',{h['keyword'] for h in classify('特别不好玩')})

    def test_body_stiffness_is_not_product_hardness(self):
        self.assertNotIn('偏硬／难捏',{h['keyword'] for h in classify('孕期手指关节僵硬，捏一捏没那么僵硬')})
        self.assertFalse(is_usable('？'))
        self.assertFalse(is_usable('y'))
        self.assertFalse(is_usable('很好吃'))

    def test_tiktok_nested_components_and_incentive_flags(self):
        component={'component_name':'product_info','component_data':{
            'product_info':{'title':'toy','desc_blocks':[{'type':'text','text':'hello'},{'type':'ul','content':['Bahan: slow rising glitter']} ]},
            'shop_info':{'seller_id':'123','on_sell_product_count':29,'shop_exp_scores':[{'score_description':'Ships within 48h','format_score':'89%'}]},
            'review_info':{'has_more':True,'total_reviews':'17','product_reviews':[
                {'review_id':'987','review_text':'Wangiiiiiii','review_rating':5,'is_incentivized_review':True}]}}}
        body={'data':{'product_data':{'page_config':{'components_map':[component]}}}}
        result=normalize_tiktok_detail(body)
        self.assertEqual(result['description_blocks'],['hello','Bahan: slow rising glitter'])
        self.assertEqual(result['review_total'],'17')
        self.assertTrue(result['unique_reviews'][0]['is_incentivized_review'])
        self.assertEqual(result['shop_profile']['on_sell_product_count'],29)
        self.assertEqual(result['shop_profile']['shop_exp_scores'][0]['format_score'],'89%')
        self.assertIn('香味（买家描述）',{h['keyword'] for h in classify(result['unique_reviews'][0]['text'])})

    def test_deduplication_and_document_frequency(self):
        review={'review_key':'x','text':'漏了漏了，玩了一天就漏了','rating':2}
        result=analyze_product({'product_id':'1','unique_reviews':[review,review]},[])
        self.assertEqual(result['sample_statistics']['unique_reviews'],1)
        self.assertEqual(next(k['review_count'] for k in result['keyword_frequencies'] if k['polarity']=='risk'),1)

    def test_material_extraction_retains_claim_source(self):
        ocr=[{'image_path':'douyin-1-00.jpg','sha256':'abc','lines':['TPR 外 壳','麦 芽 糖 填 充']}]
        result=analyze_product({'product_id':'1','description_blocks':['Bahan: slow rising glitter'],'unique_reviews':[]},ocr)
        self.assertEqual({r['material_label'] for r in result['material_mentions']},{'TPR','麦芽糖填充（商家描述）'})
        self.assertTrue(all(r['evidence_status']=='merchant_claim' for r in result['material_mentions']))
        unknown=analyze_product({'product_id':'2','description_blocks':['slow rising, lembut'],'unique_reviews':[]},[])
        self.assertEqual(unknown['material_mentions'],[])

    def test_cache_redaction_removes_buyer_identifiers(self):
        result=redact({'token':'secret','reviewer_id':'a','reviewer_name':'b','reviewer_avatar_url':'c',
                       'review_text':'hello','seller_id':'shop','nested':{'authorization':'private'}},'secret')
        self.assertEqual(result,{'review_text':'hello','seller_id':'shop','nested':{}})


if __name__=='__main__':
    unittest.main()
