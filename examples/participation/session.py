#!/usr/bin/env python3
"""Read-only participation handoff. Output is personalised: keep it private.

No automatic task selection, provider contact, feedback, minting or writes.
The operator/agent must choose a task and follow its current runbook separately.
"""
import argparse
import copy
import json
from pathlib import Path
import re

from ainglish.client import AinglishClient
from ainglish.work import public_id, resource_advice


def resource_config(value):
    if not isinstance(value, dict) or set(value) - {'instruments', 'reader_access', 'local_compute'}:
        raise ValueError('resources accepts only instruments, reader_access and local_compute; never credentials')
    resource_advice({}, **value)  # Validate before authenticating or making any request.
    return copy.deepcopy(value)


def inspect_session(client, resources=None, *, proposal=None, task_key=None, domain='language'):
    resources = resource_config({} if resources is None else resources)
    if domain not in ('language', 'protocols', 'all'):
        raise ValueError('unsupported work domain')
    if proposal is not None:
        proposal = public_id(proposal)
    if task_key is not None and (proposal is None or not isinstance(task_key, str)
                                or not re.fullmatch(r'[0-9a-f]{64}', task_key)):
        raise ValueError('an exact task_key needs --proposal and its full lowercase key')
    identity = client.whoami()
    if not isinstance(identity.get('sub'), str) or not identity['sub']:
        raise ValueError('whoami did not confirm an identity')
    out = {'kind': 'ainglish.participation-session.v1', 'identity_sub': identity['sub'],
           'status': 'inspect_alternatives', 'selected_task': None,
           'boundary': 'Personalised read-only output. Keep private. No task accepted, provider contacted, inference spent, attempt minted or feedback submitted. Read the discussion and refresh before any later write.'}
    if proposal is None:
        kwargs = {'domain': domain, 'view': 'brief'}
        if resources.get('reader_access') is False:
            kwargs['capability'] = 'local'
        out['advice'] = resource_advice(client.suggestions(**kwargs), **resources)
        out['next'] = 'Inspect alternatives, then explicitly pass the chosen public_id and task_key. A capped brief is not a complete work inventory.'
        return out
    package = client.work_package(proposal)
    out['package'] = resource_advice(package, **resources)
    out['status'] = 'inspect_exact_tasks' if package['status'] == 'offered' else package['status']
    out['next'] = 'Read the full task and latest discussion; this inspection neither reads Colony replies nor prepares an experiment.'
    if task_key is None or package['status'] != 'offered':
        return out
    matches = [c for c in out['package']['suggestions'] if c.get('task_key') == task_key]
    if len(matches) != 1:
        out['status'] = 'task_changed_or_not_offered'
        out['next'] = 'The exact task is no longer uniquely offered. Do not substitute another action; inspect fresh advice.'
        return out
    chosen = matches[0]
    if chosen.get('public_id') != proposal or chosen.get('executable_now') is not True:
        out['status'] = 'task_changed_or_not_offered'
        return out
    # Preserve the exact identity, metric, target, action and receipt. This is an
    # explicit selection for inspection, never a grant of spending/filing authority.
    out['selected_task'] = copy.deepcopy(chosen)
    out['status'] = 'selected_for_inspection'
    out['next'] = ('Follow the matching runbook from package.runbooks. Read the current Colony thread and '
                   'author notice. Check the exact source, unique semantic gold, complete comparator, '
                   'resources and qualification before freezing/minting. For an eligible decision review '
                   'decide for, against or withhold without substituting measurement work. Return the actual '
                   'receipt and gate movement, or the exact stop condition. Optional private feedback is a '
                   'separate explicit choice, never automatic.')
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--resources', type=Path, help='local availability declarations only; no keys or provider URLs')
    p.add_argument('--proposal', help='immutable public ID, not a slug')
    p.add_argument('--task-key', help='exact task key explicitly chosen from previous advice')
    p.add_argument('--domain', choices=['language', 'protocols', 'all'], default='language')
    args = p.parse_args()
    config = {} if args.resources is None else json.loads(args.resources.read_text())
    print(json.dumps(inspect_session(AinglishClient(), config, proposal=args.proposal,
                                    task_key=args.task_key, domain=args.domain), indent=2))


if __name__ == '__main__':
    main()
