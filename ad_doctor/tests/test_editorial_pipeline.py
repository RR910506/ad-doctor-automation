from copy import deepcopy
import json
from unittest import TestCase
from unittest.mock import Mock

from src.services.editorial_pipeline import candidate_fingerprint, process_topic


ROUTES = {'research': {'provider': 'perplexity', 'model': 'sonar'},
          'writing': {'provider': 'openai', 'model': 'writer'},
          'quality': {'provider': 'anthropic', 'model': 'reviewer'}}
QUOTE = 'Acme launched a refill campaign inviting customers to return empty bottles for reuse.'


def candidate():
    return {'topic': 'Acme launches refill campaign', 'sources': [
        {'source_name': f'Publication {index}', 'publisher_network': f'group_{index}',
         'article_url': f'https://publisher{index}.example/article', 'article_text': (QUOTE + ' ') * 5,
         'headline': 'Acme launches refill campaign', 'approved': True, 'active_status': 1}
        for index in range(1, 4)]}


def research():
    return {'same_development': True, 'facts': [{'source_id': f'source_{index}', 'quote': QUOTE} for index in range(1, 4)],
            'unknowns': ['Business results have not been reported.'], 'contradictions': []}


def block():
    return {'text': 'Our view is that return participation may build a refill habit.',
            'kind': 'doctor_opinion', 'evidence_ids': ['evidence_1']}


def draft():
    return {'title': 'The refill habit', 'diagnosis': block(), 'doctors_opinion': block(),
            'prescription': block(), 'article': [block(), block(), block()], 'caption': block(),
            'carousel': [{'slide': index, 'title': 'The refill habit', **block()} for index in range(1, 9)]}


def registry(outputs):
    result = Mock()
    result.generate_json.side_effect = [{'data': output, 'usage': {'output_tokens': 50}} for output in outputs]
    return result


PASS = {'passed': True, 'issues': [], 'summary': 'Claims supported.'}


class EditorialPipelineTests(TestCase):
    def test_distinct_routes_and_evidence_only_writer_input(self):
        provider = registry([research(), draft(), PASS])
        result = process_topic(candidate(), provider, ROUTES)
        self.assertEqual(result['status'], 'pending_review')
        calls = provider.generate_json.call_args_list
        self.assertEqual([call.kwargs['provider'] for call in calls], ['perplexity', 'openai', 'anthropic'])
        self.assertEqual([call.kwargs['model'] for call in calls], ['sonar', 'writer', 'reviewer'])
        self.assertNotIn('documents', calls[1].kwargs['payload'])
        self.assertIn('validated_evidence_pack', calls[1].kwargs['payload'])
        self.assertEqual(len(result['evidence']['source_reports']), 3)
        self.assertFalse('approved' in result)

    def test_invented_quote_stops_before_writing(self):
        value = research()
        value['facts'][0]['quote'] = 'The campaign generated 900 million sales during the first week of operation.'
        provider = registry([value])
        result = process_topic(candidate(), provider, ROUTES)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('not found', result['errors'][0])
        self.assertEqual(provider.generate_json.call_count, 1)

    def test_same_network_inactive_and_unapproved_sources_cannot_qualify(self):
        for field, value in [('publisher_network', 'group_1'), ('approved', False), ('active_status', 0)]:
            with self.subTest(field=field):
                source = candidate()
                source['sources'][2][field] = value
                provider = registry([])
                self.assertEqual(process_topic(source, provider, ROUTES)['status'], 'blocked')
                provider.generate_json.assert_not_called()

    def test_research_quotes_must_cover_three_networks(self):
        value = research()
        value['facts'][2]['source_id'] = 'source_1'
        result = process_topic(candidate(), registry([value]), ROUTES)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('three publisher groups', result['errors'][0])

    def test_malformed_references_hold_draft_instead_of_crashing(self):
        for value in (42, None, 'evidence_1', [['evidence_1']], {'id': 'evidence_1'}):
            with self.subTest(value=value):
                package = draft()
                package['article'][0]['evidence_ids'] = value
                provider = registry([research(), package])
                result = process_topic(candidate(), provider, ROUTES)
                self.assertEqual(result['status'], 'needs_revision')
                self.assertFalse(result['quality']['passed'])
                self.assertEqual(provider.generate_json.call_count, 2)

    def test_unsupported_number_and_title_block_before_critic(self):
        for location in ('title', 'opinion'):
            with self.subTest(location=location):
                package = draft()
                if location == 'title':
                    package['title'] = 'Sales jump by 300%'
                else:
                    package['doctors_opinion']['text'] = 'Sales increased by 300%.'
                provider = registry([research(), package])
                result = process_topic(candidate(), provider, ROUTES)
                self.assertEqual(result['status'], 'needs_revision')
                self.assertEqual(provider.generate_json.call_count, 2)

    def test_critic_cannot_pass_with_unresolved_issues(self):
        provider = registry([research(), draft(), {'passed': True, 'issues': ['Unsubstantiated inference.'], 'summary': 'Revise.'}])
        result = process_topic(candidate(), provider, ROUTES)
        self.assertEqual(result['status'], 'needs_revision')
        self.assertFalse(result['quality']['passed'])

    def test_fingerprint_is_order_independent_and_tracks_source_updates(self):
        source = candidate()
        identity = candidate_fingerprint(source)
        source['sources'].reverse()
        self.assertEqual(candidate_fingerprint(source), identity)
        source['sources'][0]['article_text'] += ' Updated.'
        self.assertNotEqual(candidate_fingerprint(source), identity)

    def test_provider_failure_does_not_leak_details_or_call_other_providers(self):
        provider = registry([])
        provider.generate_json.side_effect = RuntimeError('SECRET_MARKER')
        result = process_topic(candidate(), provider, ROUTES)
        self.assertEqual(result['status'], 'blocked')
        self.assertNotIn('SECRET_MARKER', json.dumps(result))
        self.assertEqual(provider.generate_json.call_count, 1)

    def test_persistence_failure_stops_before_writing(self):
        provider = registry([research()])
        def fail(_):
            raise RuntimeError('Storage is unavailable')
        with self.assertRaises(RuntimeError):
            process_topic(candidate(), provider, ROUTES, checkpoint_callback=fail)
        self.assertEqual(provider.generate_json.call_count, 1)

    def test_revision_resumes_research_and_includes_critic_issues(self):
        first = registry([research(), draft(), {'passed': False, 'issues': ['Prescription needs a test plan.'], 'summary': 'Revise.'}])
        checkpoint = process_topic(candidate(), first, ROUTES)
        retry = registry([draft(), PASS])
        result = process_topic(candidate(), retry, ROUTES, checkpoint=checkpoint)
        self.assertEqual(result['status'], 'pending_review')
        self.assertEqual(retry.generate_json.call_count, 2)
        self.assertEqual(retry.generate_json.call_args_list[0].kwargs['payload']['previous_review_issues_to_correct'], ['Prescription needs a test plan.'])

    def test_changing_only_quality_provider_reuses_evidence_and_draft(self):
        first = registry([research(), draft()])
        first.generate_json.side_effect = [{'data': research(), 'usage': {}}, {'data': draft(), 'usage': {}}, RuntimeError('Temporary')]
        checkpoint = process_topic(candidate(), first, ROUTES)
        changed = deepcopy(ROUTES)
        changed['quality'] = {'provider': 'gemini', 'model': 'review-other'}
        retry = registry([PASS])
        result = process_topic(candidate(), retry, changed, checkpoint=checkpoint)
        self.assertEqual(result['status'], 'pending_review')
        self.assertEqual(retry.generate_json.call_count, 1)
        self.assertEqual(retry.generate_json.call_args.kwargs['provider'], 'gemini')

    def test_changing_research_provider_researches_again(self):
        initial = process_topic(candidate(), registry([research(), draft(), PASS]), ROUTES)
        changed = deepcopy(ROUTES)
        changed['research'] = {'provider': 'gemini', 'model': 'research-other'}
        retry = registry([research(), draft(), PASS])
        result = process_topic(candidate(), retry, changed, checkpoint=initial)
        self.assertEqual(result['status'], 'pending_review')
        self.assertEqual(retry.generate_json.call_count, 3)
