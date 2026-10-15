"""Offline, report-only review of a prospective comprehension instrument.

This is neither qualification nor scientific approval. It does not mint, call readers,
alter assignment, rescore results, or change the server's admission rules.
"""
from collections import Counter, defaultdict
from itertools import product
import re

from ainglish.panel import arm_for
from ainglish.experiment_audit import audit_items


def _normal(text):
    return ' '.join(re.findall(r"\w+", text.casefold()))


def _requirements_report(manifest, real, readers, requirements, flag):
    """Check explicit prospective design declarations; never invent acceptance gates."""
    if requirements is None:
        return None
    allowed = {'required_options', 'arm_quotas', 'opposite_arm_frame_axis'}
    if not isinstance(requirements, dict) or set(requirements) - allowed:
        raise ValueError('requirements must name only required_options, arm_quotas, opposite_arm_frame_axis')
    required = requirements.get('required_options', [])
    if not isinstance(required, list) or any(not isinstance(x, str) or not x.strip() for x in required):
        raise ValueError('required_options must be a list of non-empty answer texts')
    normalized = [x.strip().casefold() for x in required]
    if len(normalized) != len(set(normalized)):
        raise ValueError('required_options must be unique after harness normalization')
    quotas = requirements.get('arm_quotas', [])
    if not isinstance(quotas, list):
        raise ValueError('arm_quotas must be a list')
    frame_axis = requirements.get('opposite_arm_frame_axis')
    if frame_axis is not None and (not isinstance(frame_axis, str) or not frame_axis.strip()):
        raise ValueError('opposite_arm_frame_axis must be a non-empty metadata field name')
    names = [r.get('name') for r in readers]
    valid_ids = all(isinstance(i.get('id'), str) and i['id'].strip() for i in real)
    valid_names = [n for n in names if isinstance(n, str) and n.strip()]
    identity_ok = (bool(real and readers) and valid_ids and len({i['id'].strip() for i in real}) == len(real)
                   and len(valid_names) == len(names)
                   and len({n.strip().casefold() for n in valid_names}) == len(names))
    result = {'answer_menus': [], 'arm_quotas': [], 'frame_pairs': [],
              'assignment_assessed': identity_ok}
    for item in real:
        if not required:
            break
        options = item.get('options')
        if not isinstance(options, list) or any(not isinstance(x, str) for x in options):
            flag('required_options_unassessed', item_id=item.get('id'))
            continue
        offered = {x.strip().casefold() for x in options}
        missing = [text for text, norm in zip(required, normalized) if norm not in offered]
        result['answer_menus'].append({'item_id': item.get('id'), 'missing_options': missing})
        if missing:
            flag('required_option_missing', item_id=item.get('id'), missing_options=missing)
    for quota in quotas:
        if (not isinstance(quota, dict) or set(quota) != {'reader', 'where', 'english', 'ainglish'}
                or not isinstance(quota['reader'], str) or not quota['reader'].strip()
                or not isinstance(quota['where'], dict)
                or any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
                       for k, v in quota['where'].items())
                or any(type(quota[a]) is not int or quota[a] < 0 for a in ('english', 'ainglish'))):
            raise ValueError('each arm quota needs reader, explicit string-valued where, and nonnegative integer arm counts')
        expected = {a: quota[a] for a in ('english', 'ainglish')}
        row = {'reader': quota['reader'], 'where': dict(quota['where']),
               'expected': expected, 'observed': None}
        result['arm_quotas'].append(row)
        if not identity_ok or quota['reader'] not in names:
            flag('arm_quota_unassessed', **row)
            continue
        if any(any(k not in i or not isinstance(i[k], str) or not i[k].strip()
                   for k in quota['where']) for i in real):
            flag('arm_quota_unassessed', **row)
            continue
        selected = [i for i in real if all(i[k] == v for k, v in quota['where'].items())]
        if not selected:
            flag('arm_quota_empty_selection', **row)
            continue
        counts = Counter(arm_for(manifest.get('seed', 0), quota['reader'], i['id']) for i in selected)
        row['observed'] = {a: counts[a] for a in ('english', 'ainglish')}
        if row['observed'] != expected:
            flag('arm_quota_mismatch', **row)
    if frame_axis is not None:
        if not identity_ok:
            flag('frame_assignment_unassessed', axis=frame_axis)
        else:
            groups = defaultdict(list)
            for item in real:
                frame = item.get(frame_axis)
                if not ((isinstance(frame, str) and frame.strip()) or type(frame) is int):
                    flag('frame_identity_missing', axis=frame_axis, item_id=item.get('id'))
                else:
                    groups[frame].append(item)
            for name in names:
                for frame, group in sorted(groups.items(), key=lambda pair: (type(pair[0]).__name__, str(pair[0]))):
                    counts = Counter(arm_for(manifest.get('seed', 0), name, i['id']) for i in group)
                    row = {'reader': name, 'axis': frame_axis, 'frame': frame,
                           'item_ids': [i['id'] for i in group],
                           'english': counts['english'], 'ainglish': counts['ainglish']}
                    result['frame_pairs'].append(row)
                    if len(group) != 2:
                        flag('frame_not_pair', **row)
                    elif counts['english'] != 1 or counts['ainglish'] != 1:
                        flag('paired_frame_same_arm', **row)
    return result


def check(manifest, *, axes=('settlement_stratum', 'probe', 'domain'), requirements=None):
    """Inspect a complete, local manifest; never fetch URLs or use reader credentials.

    Axes are explicit item metadata, not inferred scientific classifications. Their
    Cartesian product is reported, including empty intersections. A lexical answer
    overlap is a review flag, never proof of leakage. Repeated semantic_frame values
    are author-declared clustering, not machine detection of semantic equivalence.
    Optional requirements compare declared answer menus, exact arm quotas and
    opposite-arm frame pairs against the fixed design. Mismatches are report-only.
    """
    if not isinstance(manifest, dict):
        raise ValueError('manifest must be an object')
    if not isinstance(axes, (tuple, list)) or not all(isinstance(a, str) and a for a in axes) or len(set(axes)) != len(axes):
        raise ValueError('axes must be distinct non-empty field names')
    if len(axes) > 4:
        raise ValueError('at most four explicitly chosen axes may be inspected')
    findings = []

    def flag(code, **details):
        findings.append({'code': code, **details})

    items = manifest.get('items')
    if 'panel' in manifest and 'readers' in manifest and manifest['panel'] != manifest['readers']:
        raise ValueError('panel and readers must not declare different reader rosters')
    readers = manifest.get('panel', manifest.get('readers'))
    if not isinstance(items, list) or not isinstance(readers, list):
        raise ValueError('provide local items and panel (or committed readers) arrays; URLs are not fetched')
    if any(not isinstance(i, dict) for i in items + readers):
        raise ValueError('items and readers must be objects')
    real = [i for i in items if not i.get('calibration')]
    if not real or not readers:
        flag('empty_design', targets=len(real), readers=len(readers))
    ids = [i.get('id') for i in items]
    names = [r.get('name') for r in readers]
    for kind, values in [('item', ids), ('reader', names)]:
        valid = [v for v in values if isinstance(v, str) and v.strip()]
        if len(valid) != len(values):
            flag('invalid_identity', identity_kind=kind)
        normalized = [v.strip().casefold() if kind == 'reader' else v.strip() for v in valid]
        duplicates = sorted(k for k, count in Counter(normalized).items() if count > 1)
        if duplicates:
            flag('duplicate_identity', identity_kind=kind, identities=duplicates)

    missing_axes = {axis: [i.get('id') for i in real if not isinstance(i.get(axis), str) or not i[axis].strip()] for axis in axes}
    for axis, missing in missing_axes.items():
        if missing:
            flag('unclassified_axis', axis=axis, item_ids=missing)
    active_axes = [a for a in axes if not missing_axes[a]]
    counts = defaultdict(Counter)
    by_reader = defaultdict(Counter)
    for r in readers:
        if not isinstance(r.get('name'), str) or not r['name'].strip():
            continue
        for item in real:
            if not isinstance(item.get('id'), str) or not item['id'].strip():
                continue
            arm = arm_for(manifest.get('seed', 0), r['name'], item['id'])
            by_reader[r['name']][arm] += 1
            key = (r['name'], *(item[a] for a in active_axes))
            counts[key][arm] += 1
    levels = [sorted({i[a] for i in real}) for a in active_axes]
    size = len(readers)
    for level in levels:
        size *= len(level)
    cells = []
    if size > 10000:
        flag('crossproduct_not_enumerated', planned_cells=size, limit=10000)
    else:
        for name in sorted(by_reader):
            for values in product(*levels):
                c = counts[(name, *values)]
                cell = {'reader': name, 'axes': dict(zip(active_axes, values)),
                        'english': c['english'], 'ainglish': c['ainglish']}
                cells.append(cell)
                if not c:
                    flag('empty_crossed_cell', **cell)
                elif not c['english'] or not c['ainglish']:
                    flag('single_arm_cell', **cell)
    for name, c in sorted(by_reader.items()):
        if not c['english'] or not c['ainglish']:
            flag('single_arm_reader', reader=name, english=c['english'], ainglish=c['ainglish'])

    frames = defaultdict(list)
    missing_frames = []
    for item in real:
        iid = item.get('id')
        frame = item.get('semantic_frame')
        if isinstance(frame, str) and frame.strip():
            frames[frame].append(iid)
        else:
            missing_frames.append(iid)
        answer = item.get('answer')
        options = item.get('options')
        if ('answer' not in item or not isinstance(options, list)
                or any(not isinstance(option, str) for option in options)
                or [option.strip().casefold() for option in options].count(str(answer).strip().casefold()) != 1):
            flag('invalid_answer_key', item_id=iid)
            continue
        gold = _normal(str(answer))
        # Short choices such as "yes" or an opaque code are not a meaningful lexical
        # leakage screen. The declared limits must stay visible in the report.
        if len(gold.split()) < 3:
            continue
        overlap = [arm for arm in ('english', 'ainglish') if isinstance(item.get(arm), str) and
                   (' ' + gold + ' ') in (' ' + _normal(item[arm]) + ' ')]
        if overlap:
            flag('gold_phrase_in_arm_review', item_id=iid, arms=overlap)
    for frame, group in sorted(frames.items()):
        if len(group) > 1:
            flag('shared_declared_frame', semantic_frame=frame, item_ids=group)
    if missing_frames:
        flag('semantic_independence_unassessed', item_ids=missing_frames)
    requirements_report = _requirements_report(manifest, real, readers, requirements, flag)
    return {
        'kind': 'ainglish.instrument-audit.v1', 'report_only': True,
        'target_items': len(real), 'reader_count': len(readers),
        'requested_axes': list(axes), 'assessed_axes': active_axes,
        'item_audit': audit_items(items),
        'cells': cells, 'findings': findings,
        'requirements': requirements_report,
        'limits': [
            'No finding is an automatic invalidation, qualification, admission decision, or evidence result.',
            'Empty intersections may be intentional; tiny cells may lack both arms by chance. Review against the declared design.',
            'Missing axis or frame metadata is unassessed, not evidence of balance or independence.',
            'Frame identity is supplied by the author. Different frame labels do not prove semantic independence.',
            'Lexical overlap checks only complete normalized gold phrases of at least three words; it can miss shortcuts and can flag legitimate text.',
            'No outputs were inspected; balance cannot establish a sound comparator, key, estimand, power, or model independence.',
            'Requirements are caller-supplied design checks, not automatic or retrospective governance gates. Missing declarations are unassessed.',
            'Required choices only check that declared rival answers are offered. Their presence does not prove absence of answer-menu shortcuts.',
            'Opposite-arm frame pairs do not prove independent observations; preserve declared clustering in the uncertainty plan.',
        ],
    }
