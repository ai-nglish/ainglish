"""Stable-ID writes work against slug-only servers without retrying a mutation."""
import unittest
from ainglish.client import AinglishClient

PID='a-12345678abcdefgh'
class Probe(AinglishClient):
    def __init__(self):
        super().__init__(use_env=False);self.calls=[];self.slug='exact-current';self.changed=False
    def get(self,path,auth=False):
        self.calls.append(('GET',path,auth))
        if path.endswith('/slug-history'):return {'proposal_public_id':PID,'current_slug':self.slug}
        return {'public_id':'a-0000000000000000' if self.changed else PID,'slug':self.slug,'stage':'superseded','superseded_by':'do-not-follow'}
    def post(self,path,payload,**kwargs):
        self.calls.append(('POST',path,payload,kwargs));return {'receipt':'fixture'}

class ProposalWriteIdentityTests(unittest.TestCase):
    def operations(self):
        return [lambda c,r:c.vote(r,-1),lambda c,r:c.second(r),
                lambda c,r:c.replace_vote(r,1,'A new independent assessment.'),
                lambda c,r:c.withdraw_vote(r,'An independence conflict.'),
                lambda c,r:c.withdraw_second(r,'The study needs revision.'),
                lambda c,r:c.withdraw(r,reason='filed_in_error'),
                lambda c,r:c.retire(r,'This version is no longer pursued.'),
                lambda c,r:c.amend(r,title='Revised title'),
                lambda c,r:c.set_author_work_notice(r,'pause_measurements','Review needed.',expected_content_digest='a'*64,expected_notice_id=None,idempotency_key='identity-notice')]
    def test_each_write_binds_id_to_exact_record_without_successor_following(self):
        for operation in self.operations():
            for ref in [PID,PID.upper(),'https://ainglish.org/proposals/'+PID]:
                with self.subTest(operation=operation,ref=ref):
                    c=Probe();operation(c,ref)
                    self.assertEqual(('GET','/api/v1/proposals/'+PID+'/slug-history',False),c.calls[0])
                    self.assertEqual(('GET','/api/v1/proposals/exact-current',True),c.calls[1])
                    self.assertEqual(1,sum(x[0]=='POST' for x in c.calls))
                    self.assertIn('/exact-current/',c.calls[-1][1]);self.assertNotIn('do-not-follow',c.calls[-1][1])
    def test_identity_mismatch_and_foreign_urls_make_no_write(self):
        for operation in self.operations():
            for ref,changed in [(PID,True),('https://example.invalid/proposals/'+PID,False)]:
                c=Probe();c.changed=changed
                with self.assertRaises(ValueError):operation(c,ref)
                self.assertFalse(any(x[0]=='POST' for x in c.calls))
    def test_slug_path_quoting_and_no_resolution_cache(self):
        c=Probe();c.vote('opaque/name',-1);self.assertEqual('/api/v1/proposals/opaque%2Fname/vote',c.calls[0][1])
        c=Probe();c.vote(PID,-1);c.slug='renamed-current';c.vote(PID,1)
        self.assertEqual('/api/v1/proposals/renamed-current/vote',c.calls[-1][1])
        self.assertEqual(4,sum(x[0]=='GET' for x in c.calls))
    def test_report_normalizes_outer_reference_not_measurement_identity(self):
        c=Probe();target={'type':'measurement','id':'exact-attempt-id'}
        c.report_content(PID,'other',target=target,idempotency_key='identity-report')
        self.assertEqual('exact-current',c.calls[-1][2]['proposal']);self.assertEqual(target,c.calls[-1][2]['target'])
    def test_related_public_reads_do_not_attach_authentication(self):
        for method in ('history','author_work_notices'):
            c=Probe();getattr(c,method)(PID)
            self.assertEqual(3,len(c.calls));self.assertTrue(all(x[0]=='GET' and not x[2] for x in c.calls))
    def test_duplicate_and_proposal_report_references_are_checked(self):
        c=Probe();c.withdraw('original',reason='duplicate',canonical_slug=PID)
        self.assertEqual('exact-current',c.calls[-1][2]['canonical_slug'])
        c=Probe();c.report_content(PID,'other',target={'type':'proposal','id':PID},idempotency_key='identity-report')
        self.assertEqual({'type':'proposal','id':'exact-current'},c.calls[-1][2]['target'])
        c=Probe();c.changed=True
        with self.assertRaises(ValueError):
            c.withdraw('original',reason='duplicate',canonical_slug=PID)
        self.assertFalse(any(x[0]=='POST' for x in c.calls))
    def test_custodial_preview_resolves_exact_id_without_changing_preview(self):
        c=Probe();c.custodial_amend(PID,'Original author unavailable; surface-only repair.',title='Revised')
        self.assertEqual('/api/v1/moderation/proposals/exact-current/custodial-amend?dry_run=1',c.calls[-1][1])
    def test_mutation_error_is_never_retried(self):
        class Failing(Probe):
            def post(self,*args,**kwargs):
                super().post(*args,**kwargs)
                raise RuntimeError('the server refused this exact write')
        c=Failing()
        with self.assertRaises(RuntimeError):c.vote(PID,-1)
        self.assertEqual(1,sum(x[0]=='POST' for x in c.calls))
if __name__=='__main__':unittest.main()
