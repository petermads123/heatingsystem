export const meta = {
  name: 'round',
  description: 'Run one round of a feature: conceptualize, plan, implement, verify, test, concept-check, merge',
  whenToUse: 'Started by the /round skill, never directly. One round at a time.',
  phases: [
    { title: 'Conceptualize', detail: 'round scope and acceptance criteria', model: 'opus' },
    { title: 'Plan', detail: 'modules, signatures, test intents', model: 'opus' },
    { title: 'Implement', detail: 'the code', model: 'sonnet' },
    { title: 'Verify', detail: 'ruff, mypy, parallel code review', model: 'sonnet' },
    { title: 'Test design', detail: 'parallel edge-case designers', model: 'sonnet' },
    { title: 'Write tests', detail: 'the suite', model: 'sonnet' },
    { title: 'Run tests', detail: 'full suite, fix loop', model: 'sonnet' },
    { title: 'Concept check', detail: 'three independent checkers', model: 'sonnet' },
    { title: 'Close', detail: 'commit and merge into the feature branch', model: 'sonnet' },
  ],
}

// ---------------------------------------------------------------------------
// args (from /round):
//   feature_dir     development/feat/csv-export
//   round_dir       development/feat/csv-export/r1-writer-core
//   round           1
//   feature_branch  feat/csv-export
//   round_branch    feat/csv-export--r1   (checked out before the run)
//   start_step      1..9, where to begin; earlier steps are already done
//   answers         [{ step, question, answer }] from earlier escalations
//   attempt         fix attempts already spent this round (round.json "attempt"), default 0
//   kind            "fix" for a round of a /fix (brief.json kind), else "feature"
//   adopt_repro     true for the fix round that moves diagnosis/test_reproduction.py into tests/
//                   (it still exists on the feature branch); false for every later fix round
//
// When this returns needs_input or failed, round.json may not say so yet (the script itself
// cannot write files): /round records it before doing anything else.
//
// Returns one of:
//   { status: 'merged', summary, merge_commit }
//   { status: 'needs_input', step, step_name, question, summary }
//   { status: 'failed', step, step_name, summary }
// ---------------------------------------------------------------------------

const STEPS = [
  'Conceptualize', 'Plan', 'Implement', 'Verify', 'Test design',
  'Write tests', 'Run tests', 'Concept check', 'Close',
]
const MAX_FIX_ATTEMPTS = 2
const TEST_LENSES = ['input-space', 'contract', 'failure-paths', 'integration']
const CHECKERS = 3

const a = args || {}
for (const key of ['feature_dir', 'round_dir', 'round', 'feature_branch', 'round_branch']) {
  if (a[key] === undefined || a[key] === '') throw new Error(`round workflow: missing args.${key}`)
}
const START = Number(a.start_step === undefined ? 1 : a.start_step)
if (!Number.isInteger(START) || START < 1 || START > 9) {
  throw new Error(`round workflow: start_step must be 1..9, got ${a.start_step}`)
}
const SAME_BRANCH = a.round_branch === a.feature_branch
const FIX = a.kind === 'fix'
const ADOPT = FIX && a.adopt_repro === true
const REPRO = `${a.feature_dir}/diagnosis/test_reproduction.py`

// The extra rules a fix round follows; empty for a feature round. Only the round that adopts
// the reproduction runs it red first; later rounds of a fix keep it green.
const LATER_FIX_RULE = '\nFIX ROUND (a later one): the reproduction already lives in tests/ and is green; keep it ' +
  'green. Work at the root cause in the Defect block (BRIEF.md), within the chosen scope, with a test for every class ' +
  'site this round covers.'
const ADOPT_RULES = {
  1: `\nFIX ROUND: start from the Defect block in BRIEF.md and the chosen scope. AC1 is "the reproduction ` +
    `(${REPRO}, moved into tests/) passes"; the last AC is "no regression in the chosen scope", naming the class ` +
    'sites it covers. Plan nothing beyond the chosen scope.',
  3: `\nFIX ROUND: before any fix, move the reproduction into the suite (git mv ${REPRO} into tests/, or merge it into ` +
    'the module\'s test file), run it and record the RED output in log.md. If it already passes, stop and return a ' +
    'question: the diagnosis no longer holds. Then fix at the root cause the Defect block names - not where the ' +
    'symptom shows - run it GREEN and record that output too.',
  8: '\nFIX ROUND: also require, in log.md, the recorded red run and green run of the reproduction; that the change ' +
    'is at the root cause, not the site of the symptom; and a test for every class site the chosen scope covers. ' +
    'A green suite alone does not pass a fix round.',
}
const fixRule = step => (!FIX ? '' : ADOPT ? ADOPT_RULES[step] || '' : [1, 3, 8].includes(step) ? LATER_FIX_RULE : '')
const ANSWERS = Array.isArray(a.answers) ? a.answers : []

const RESULT = {
  type: 'object',
  properties: {
    status: { type: 'string', enum: ['done', 'question', 'fail'] },
    summary: { type: 'string', description: 'Two to five lines: what this step produced (the trace)' },
    question: { type: 'string', description: 'When status is question: one precise question' },
  },
  required: ['status', 'summary'],
}
const SCOPE = {
  type: 'object',
  properties: {
    status: { type: 'string', enum: ['done', 'question', 'fail'] },
    summary: { type: 'string' },
    question: { type: 'string' },
    units: {
      type: 'array',
      maxItems: 5,
      items: {
        type: 'object',
        properties: {
          name: { type: 'string' },
          files: { type: 'array', items: { type: 'string' } },
          symbols: { type: 'array', items: { type: 'string' } },
        },
        required: ['name', 'files', 'symbols'],
      },
    },
  },
  required: ['status', 'summary', 'units'],
}
const FINDINGS = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          severity: { type: 'string', enum: ['blocking', 'minor'] },
          where: { type: 'string' },
          issue: { type: 'string' },
          fix: { type: 'string' },
        },
        required: ['severity', 'where', 'issue'],
      },
    },
  },
  required: ['findings'],
}
const CASES = {
  type: 'object',
  properties: {
    cases: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          target: { type: 'string' },
          case: { type: 'string' },
          inputs: { type: 'string' },
          expected: { type: 'string' },
          priority: { type: 'string', enum: ['high', 'medium', 'low'] },
        },
        required: ['target', 'case', 'expected', 'priority'],
      },
    },
  },
  required: ['cases'],
}
const TEST_RUN = {
  type: 'object',
  properties: {
    passed: { type: 'boolean' },
    summary: { type: 'string', description: 'Counts and the gates that ran' },
    failures: { type: 'array', items: { type: 'string' }, description: 'One line per failing test or check, with the error' },
  },
  required: ['passed', 'summary', 'failures'],
}
const VERDICT = {
  type: 'object',
  properties: {
    pass: { type: 'boolean' },
    summary: { type: 'string' },
    findings: FINDINGS.properties.findings,
  },
  required: ['pass', 'summary', 'findings'],
}

const CONTEXT = [
  `Round ${a.round} of the feature in ${a.feature_dir}/ (read BRIEF.md and brief.json there).`,
  `This round's folder: ${a.round_dir}/ (log.md is its notepad, round.json its status).`,
  SAME_BRANCH
    ? `Branch ${a.round_branch} is checked out; this round runs directly on the feature branch (no round branch).`
    : `Branch ${a.round_branch} is checked out; it merges into ${a.feature_branch} at the Close step.`,
  'Run ruff, mypy and pytest by their bare names (or python -m ...) with the project\'s .venv active.',
  'STRUCTURE.md is only rewritten when the feature finishes: for what earlier rounds added, read the',
  '"Changes" sections of the earlier rounds\' log.md files in the feature folder.',
].join('\n')

function answersText() {
  if (ANSWERS.length === 0) return ''
  const lines = ANSWERS.map(x => `- (step ${x.step}) Q: ${x.question}\n  A: ${x.answer}`)
  return `\nAnswers already given for this round (binding):\n${lines.join('\n')}\n`
}

// What a writing step does with the state files before it returns.
function stateRules(step, next) {
  const done = next > STEPS.length
    ? 'set round.json "status" to "done" and leave "step" at 9'
    : `set round.json "step" to ${next} and "status" to "running"`
  return [
    'Before you return:',
    `- On success: ${done}, "attempt" to 0, "question" to "", "note" to one line on what you did,`,
    `  and append a "## ${step}. ${STEPS[step - 1]}" section to log.md with what you produced`,
    '  (if a resumed run finds that section already there, add to it rather than repeat the heading).',
    '- On a question (only for something genuinely unclear that the brief, the log and the answers',
    '  above do not settle): set round.json "status" to "blocked" and "question" to the question,',
    '  note in log.md what you were doing, and return status "question".',
    '- On a failure you cannot resolve: set "status" to "failed" with the reason in "note".',
    `- Then always: git add the round folder and your changes, commit "round ${a.round} step ${step}: <what>",`,
    `  and git push -u origin ${a.round_branch}. Never commit to any other branch.`,
  ].join('\n')
}

function prompt(step, task, next) {
  return `${CONTEXT}\n\nStep ${step}/${STEPS.length}: ${STEPS[step - 1]}.\n${task}\n${answersText()}\n${next === null ? '' : stateRules(step, next)}`
}

function run(step, agentType, task, opts) {
  const o = opts || {}
  return agent(prompt(step, task, o.next === undefined ? step + 1 : o.next), {
    agentType,
    model: o.model || 'sonnet',
    effort: o.effort || 'medium',
    schema: o.schema || RESULT,
    phase: STEPS[step - 1],
    label: o.label || `${STEPS[step - 1].toLowerCase()}`,
  })
}

function stop(step, r) {
  const base = { step, step_name: STEPS[step - 1], summary: (r && r.summary) || '' }
  if (!r) return { status: 'failed', ...base, summary: 'the step\'s agent did not return' }
  if (r.status === 'question') return { status: 'needs_input', ...base, question: r.question || '' }
  return { status: 'failed', ...base }
}

const ok = r => r && r.status === 'done'
const traces = []
const trace = (step, r) => {
  if (r && r.summary) {
    traces.push(`[${step} ${STEPS[step - 1]}] ${r.summary}`)
    log(`${STEPS[step - 1]}: ${r.summary.split('\n')[0]}`)
  }
}

let attempts = Number.isInteger(a.attempt) && a.attempt > 0 ? a.attempt : 0
let checkReport = ''

// A failing gate (step 7 or 8) sends its findings to the implementer, at most MAX_FIX_ATTEMPTS
// times per round, counted across resumes. After a fix round.json goes back to step 7, so a
// resumed run always re-runs the test gate on the fixed code before anything is checked again.
async function fix(step, what, findings) {
  attempts += 1
  if (attempts > MAX_FIX_ATTEMPTS) {
    return {
      status: 'question',
      summary: `${what} still failing after ${MAX_FIX_ATTEMPTS} fix attempts`,
      question: `${what} keeps failing after ${MAX_FIX_ATTEMPTS} fixes. Findings:\n${findings}\nHow should the round proceed?`,
    }
  }
  const r = await agent(prompt(step,
    `FIX MODE (attempt ${attempts}/${MAX_FIX_ATTEMPTS}). ${what} failed. Fix the root cause in the code.\n` +
    'Change a test only when the test itself is wrong, and say why in log.md; never weaken or delete a case to get green.\n' +
    `Findings:\n${findings}\n\n` +
    'Before you return:\n' +
    `- Append a "### Fix ${attempts}" subsection under the "## ${step}. ${STEPS[step - 1]}" section of log.md (create the ` +
    'section if missing): the findings in brief, the root cause, what you changed.\n' +
    `- Set round.json "step" to 7, "status" to "running", "attempt" to ${attempts}, "question" to "", "note" to one line.\n` +
    '- On a question or an unresolvable failure, use "blocked"/"failed" as in any step, keeping "attempt".\n' +
    `- git add, commit "round ${a.round} fix ${attempts}: <what>", git push -u origin ${a.round_branch}.`,
    null),
    { agentType: 'implementer', model: 'sonnet', effort: 'medium', schema: RESULT, phase: STEPS[step - 1], label: `fix ${attempts}` })
  trace(step, r)
  return r
}

// --- 1. Conceptualize ---------------------------------------------------------
if (START <= 1) {
  const r = await run(1, 'round-conceptualizer',
    'Isolate the exact scope of this round from BRIEF.md and the earlier rounds\' logs, and write the round concept ' +
    'into log.md: goal, in scope, out of scope, acceptance criteria (numbered, observable), and risks.' + fixRule(1),
    { model: 'opus', effort: 'medium' })
  trace(1, r)
  if (!ok(r)) return stop(1, r)
}

// --- 2. Plan -----------------------------------------------------------------
if (START <= 2) {
  const r = await run(2, 'round-planner',
    'Turn the round concept in log.md into a plan in log.md: modules, full public signatures with return types, ' +
    'an ordered implementation guide, and test intents, each tied to the acceptance criteria.',
    { model: 'opus', effort: 'medium' })
  trace(2, r)
  if (!ok(r)) return stop(2, r)
}

// --- 3. Implement ------------------------------------------------------------
if (START <= 3) {
  const r = await run(3, 'implementer',
    'Implement the plan in log.md. Record a "Changes" section in log.md listing every public module, class and ' +
    'function added or changed, with its signature and return type, and any deviation from the plan with the reason.' +
    fixRule(3))
  trace(3, r)
  if (!ok(r)) return stop(3, r)
}

// --- 4. Verify ---------------------------------------------------------------
if (START <= 4) {
  const scope = await run(4, 'verifier',
    'SCOPE MODE. Run ruff check, ruff format --check and mypy as pyproject.toml configures them and fix what they ' +
    'report. Then split the round\'s changed public code into at most 5 review units (related functions/classes ' +
    'together) and return them. Commit your fixes, but leave round.json "step" at 4.',
    { schema: SCOPE, next: 4, label: 'verify: static' })
  trace(4, scope)
  if (!ok(scope)) return stop(4, scope)

  const reviews = await parallel((scope.units || []).map((u, i) => () =>
    agent(prompt(4,
      `REVIEW, read-only. Unit "${u.name}": ${u.symbols.join(', ')} in ${u.files.join(', ')}.\n` +
      'Check the logic against the plan and the round\'s acceptance criteria: wrong results, unhandled inputs, ' +
      'broken contracts with callers, typing that hides a bug. Mark blocking only what is wrong, not style.',
      null),
      { agentType: 'code-reviewer', model: 'sonnet', effort: 'medium', schema: FINDINGS, phase: 'Verify', label: `review: ${u.name}` })))
  const findings = reviews.filter(Boolean).flatMap(r => r.findings || [])
  const dropped = reviews.length - reviews.filter(Boolean).length
  if (dropped) log(`verify: ${dropped} reviewer(s) returned nothing`)

  const r = await run(4, 'verifier',
    'APPLY MODE. Apply or rebut each reviewer finding below (record each decision in log.md), then re-run ruff and ' +
    'mypy until clean.\n' +
    `Findings:\n${findings.length ? findings.map(f => `- [${f.severity}] ${f.where}: ${f.issue}${f.fix ? ` -> ${f.fix}` : ''}`).join('\n') : '(none)'}`,
    { label: 'verify: apply' })
  trace(4, r)
  if (!ok(r)) return stop(4, r)
}

// --- 5 and 6. Test design, Write tests ---------------------------------------
if (START <= 6) {
  let cases = []
  if (START <= 5) {
    const designs = await parallel(TEST_LENSES.map(lens => () =>
      agent(prompt(5,
        `Read-only. Design test cases for the round's public code through the "${lens}" lens. ` +
        'Use the plan\'s test intents and the "Changes" section in log.md. Concrete inputs and expected results.',
        null),
        { agentType: 'test-designer', model: 'sonnet', effort: 'low', schema: CASES, phase: 'Test design', label: `design: ${lens}` })))
    cases = designs.filter(Boolean).flatMap(d => d.cases || [])
    log(`test design: ${cases.length} cases from ${designs.filter(Boolean).length}/${TEST_LENSES.length} designers`)
  }
  const r = await run(6, 'test-writer',
    (START <= 5
      ? 'First set round.json "step" to 6 with note "writing tests", append a "## 5. Test design" section to log.md ' +
        'listing the cases below (deduplicated), and commit. Then write the tests'
      : 'This is a resumed run: the test design is already in log.md under "## 5. Test design". Write the tests') +
    ' in tests/, one test_<module>.py per ' +
    'module, covering the plan\'s test intents and the cases. Run them once; failures are expected to surface ' +
    'bugs — do not change production code here.\n' +
    `Cases:\n${cases.length ? cases.map(c => `- [${c.priority}] ${c.target}: ${c.case}; inputs ${c.inputs || '-'}; expect ${c.expected}`).join('\n') : '(designers returned none: derive cases from the test intents; if resuming, the cases are in log.md)'}`)
  trace(6, r)
  if (!ok(r)) return stop(6, r)
}

// --- 7 and 8. Run tests, Concept check (with the fix loop) --------------------
async function runTests() {
  for (;;) {
    const t = await run(7, 'test-runner',
      'Run the full gate: pytest (whole suite), ruff check, ruff format --check, mypy. Report exactly what fails. ' +
      'Do not fix anything. Append the result to log.md under "## 7. Run tests" (one dated line per run). ' +
      'In round.json set "status" to "running", "question" to "", "note" to the result in one line, and "step" to 8 ' +
      `if everything passed, else 7. Commit "round ${a.round} step 7: <result>" and git push -u origin ${a.round_branch} either way.`,
      { schema: TEST_RUN, model: 'sonnet', effort: 'low', next: null, label: 'run tests' })
    if (!t) return { status: 'fail', summary: 'the test runner did not return' }
    log(`run tests: ${t.passed ? 'green' : 'red'} - ${t.summary.split('\n')[0]}`)
    if (t.passed) return { status: 'done', summary: t.summary }
    const f = await fix(7, 'The test gate', t.failures.join('\n'))
    if (!ok(f)) return f
  }
}

if (START <= 8) {
  for (;;) {
    if (START <= 7 || attempts > 0) {
      const t = await runTests()
      trace(7, t)
      if (!ok(t)) return stop(7, t)
    }
    const verdicts = (await parallel(Array.from({ length: CHECKERS }, (_, i) => () =>
      agent(prompt(8,
        `Read-only. You are concept checker ${i + 1} of ${CHECKERS}; the others check the same thing independently. ` +
        'Do a full check of the round against its concept (log.md, "## 1. Conceptualize") and BRIEF.md:\n' +
        '- Concept: does the result implement the round concept as agreed - in scope done, out of scope left alone, ' +
        'nothing earlier rounds delivered broken?\n' +
        '- Purpose: does it serve the round\'s goal and the feature, used the way a caller would use it? Run it.\n' +
        '- Acceptance criteria: is every AC met? Cite the code and the test that prove each one.\n' +
        'pass=false only for a real gap; mark those findings blocking, each with what would close it.' + fixRule(8),
        null),
        { agentType: 'concept-checker', model: 'sonnet', effort: 'low', schema: VERDICT, phase: 'Concept check', label: `check ${i + 1}` })
        .then(v => v && { checker: i + 1, ...v })))).filter(Boolean)
    if (verdicts.length < 2) {
      return stop(8, { status: 'fail', summary: `only ${verdicts.length} of ${CHECKERS} concept checkers returned` })
    }
    const passes = verdicts.filter(v => v.pass).length
    checkReport = verdicts.map(v =>
      `- checker ${v.checker}: ${v.pass ? 'pass' : 'FAIL'}. ${v.summary}` +
      v.findings.filter(f => f.severity === 'blocking').map(f => `\n  - blocking: ${f.where}: ${f.issue}`).join('')).join('\n')
    log(`concept check: ${passes}/${verdicts.length} pass`)
    // Three independent full checks: the majority decides. A dissenting checker's blocking
    // findings still go on the record (log.md, via the closer) for /finish and the user.
    if (passes * 2 > verdicts.length) break
    const blocking = verdicts.filter(v => !v.pass).flatMap(v => v.findings.filter(f => f.severity === 'blocking'))
    const f = await fix(8, 'The concept check',
      (blocking.map(b => `- ${b.where}: ${b.issue}${b.fix ? ` -> ${b.fix}` : ''}`).join('\n') || '(no blocking findings given)') +
      `\nVerdicts:\n${checkReport}`)
    if (!ok(f)) return stop(8, f)
  }
}

// --- 9. Close ----------------------------------------------------------------
const CLOSE = {
  type: 'object',
  properties: {
    status: { type: 'string', enum: ['done', 'question', 'fail'] },
    summary: { type: 'string' },
    question: { type: 'string' },
    merge_commit: { type: 'string' },
  },
  required: ['status', 'summary'],
}
const close = await agent(prompt(9,
  'Close the round, in this order (round-closer.md has the details):\n' +
  `1. On ${a.round_branch}: set round.json "step" to 9, "status" to "done", "attempt" to 0, "question" to "", ` +
  '"note" to "merged"; append "## 8. Concept check" (the verdicts below; if a resumed run finds it there, leave it) ' +
  `and "## 9. Close" to log.md; commit "round ${a.round} step 9: close" and git push -u origin ${a.round_branch}.\n` +
  (SAME_BRANCH
    ? '2. This round runs on the feature branch itself: there is nothing to merge or delete. Return the commit hash.\n'
    : `2. Merge ${a.round_branch} into ${a.feature_branch}, push ${a.feature_branch}, delete ${a.round_branch}.\n` +
      '3. After the merge write and commit nothing more. On a conflict follow the blocked path in round-closer.md.\n') +
  `Concept check verdicts:\n${checkReport || '(recorded in log.md by an earlier run)'}`,
  null),
  { agentType: 'round-closer', model: 'sonnet', effort: 'medium', schema: CLOSE, phase: 'Close', label: 'close' })
trace(9, close)
if (!ok(close)) return stop(9, close)

return { status: 'merged', summary: traces.join('\n'), merge_commit: close.merge_commit || '' }
