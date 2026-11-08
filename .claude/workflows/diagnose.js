export const meta = {
  name: 'diagnose',
  description: 'Diagnose a defect before anything is fixed: reproduce, hunt causes, prove the root cause, have it criticised',
  whenToUse: 'Started by the /fix skill, never directly.',
  phases: [
    { title: 'Reproduce', detail: 'a failing reproduction, run red', model: 'sonnet' },
    { title: 'Hunt', detail: 'three cause hunters in parallel', model: 'sonnet' },
    { title: 'Root cause', detail: 'one cause, proven, and its class', model: 'opus' },
    { title: 'Critique', detail: 'an adversarial read of the diagnosis', model: 'opus' },
  ],
}

// ---------------------------------------------------------------------------
// args (from /fix):
//   feature_dir   development/fix/csv-quoting
//   branch        fix/csv-quoting (checked out before the run)
//   symptom       what the user saw, expected vs actual, how to trigger it
//   start_step    1..4, where to begin; earlier steps are already in diagnosis/log.md
//   answers       [{ step, question, answer }] from earlier escalations
//
// Every writing step updates diagnosis/state.json and diagnosis/log.md, commits and pushes.
// When this returns needs_input or failed, state.json may not say so yet: /fix records it.
//
// Returns one of:
//   { status: 'diagnosed', is_bug, summary, defect }
//   { status: 'needs_input', step, step_name, question, summary }
//   { status: 'failed', step, step_name, summary }
// ---------------------------------------------------------------------------

const STEPS = ['Reproduce', 'Hunt', 'Root cause', 'Critique']
const LENSES = {
  trace: 'Trace backwards from the symptom: follow the wrong value from where it shows up to where it is first wrong. Name the first point where the data is already wrong.',
  history: 'Look at history: what changed in the code on the path of the symptom (git log -p, git log -S, git blame; git bisect against the reproduction if it is cheap). Name the change that introduced it, if one did.',
  contract: 'Look at contracts: which assumption, docstring promise, type, invariant or input range is violated, and by whom - the caller or the callee?',
}

const a = args || {}
for (const key of ['feature_dir', 'branch', 'symptom']) {
  if (!a[key]) throw new Error(`diagnose workflow: missing args.${key}`)
}
const START = Number(a.start_step === undefined ? 1 : a.start_step)
if (!Number.isInteger(START) || START < 1 || START > 4) {
  throw new Error(`diagnose workflow: start_step must be 1..4, got ${a.start_step}`)
}
const ANSWERS = Array.isArray(a.answers) ? a.answers : []
const DIR = `${a.feature_dir}/diagnosis`

const RESULT = {
  type: 'object',
  properties: {
    status: { type: 'string', enum: ['done', 'question', 'fail'] },
    summary: { type: 'string', description: 'Two to five lines: what this step found (the trace)' },
    question: { type: 'string' },
  },
  required: ['status', 'summary'],
}
const CANDIDATES = {
  type: 'object',
  properties: {
    candidates: {
      type: 'array',
      maxItems: 3,
      items: {
        type: 'object',
        properties: {
          cause: { type: 'string', description: 'The cause in one sentence, naming path:line' },
          evidence: { type: 'string', description: 'What you read or ran that shows it' },
          confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
        },
        required: ['cause', 'evidence', 'confidence'],
      },
    },
  },
  required: ['candidates'],
}
const VERDICT = {
  type: 'object',
  properties: {
    status: { type: 'string', enum: ['done', 'question', 'fail'] },
    summary: { type: 'string' },
    question: { type: 'string' },
    is_bug: { type: 'boolean', description: 'False when the code does what it promises and the expectation was wrong' },
    defect: {
      type: 'object',
      properties: {
        observed: { type: 'string' },
        expected: { type: 'string' },
        reproduction: { type: 'string', description: 'The file and the command that runs it red' },
        root_cause: { type: 'string', description: 'The cause, at its source, with path:line' },
        proof: { type: 'string', description: 'The throwaway change that turned the reproduction green, and that it was reverted' },
        class: { type: 'string', description: 'Every other input, caller or site the same cause breaks' },
        scope_options: { type: 'string', description: 'This instance vs the whole class: what each would change and cost' },
        recommended_scope: { type: 'string', enum: ['instance', 'class'] },
        bump: { type: 'string', enum: ['patch', 'minor', 'major'], description: 'patch unless the fix changes what callers get back' },
      },
      required: ['observed', 'expected', 'reproduction', 'root_cause', 'class', 'scope_options', 'recommended_scope', 'bump'],
    },
  },
  required: ['status', 'summary'],
}
const CRITIQUE = {
  type: 'object',
  properties: {
    holds: { type: 'boolean', description: 'True when the diagnosis survives: no blocking finding' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          severity: { type: 'string', enum: ['blocking', 'minor'] },
          issue: { type: 'string' },
          check: { type: 'string', description: 'What would settle it' },
        },
        required: ['severity', 'issue'],
      },
    },
  },
  required: ['holds', 'findings'],
}

const CONTEXT = [
  `A defect is being diagnosed on branch ${a.branch} (checked out). Nothing is fixed yet: the`,
  `diagnosis decides what the fix will be. The diagnosis folder is ${DIR}/ (log.md is its notepad,`,
  'state.json its status, same fields as a round.json). STRUCTURE.md maps the code.',
  `The symptom, as the user reported it:\n${a.symptom}`,
].join('\n')

function answersText() {
  if (ANSWERS.length === 0) return ''
  return '\nAnswers already given (binding):\n' +
    ANSWERS.map(x => `- (step ${x.step}) Q: ${x.question}\n  A: ${x.answer}`).join('\n') + '\n'
}

function stateRules(step, next) {
  const done = next > STEPS.length
    ? 'set state.json "status" to "done", "step" 4'
    : `set state.json "step" to ${next}, "status" to "running"`
  return [
    'Before you return:',
    `- On success: ${done}, "question" "", "note" one line; append "## ${step}. ${STEPS[step - 1]}" to log.md`,
    '  with what you found (if a resumed run finds it there, add to it).',
    '- On a question (only what the symptom, the code and the answers above cannot settle): "status" "blocked",',
    '  "question" set; return status "question".',
    '- On a failure you cannot resolve: "status" "failed", the reason in "note".',
    `- Then: git add ${DIR}, commit "diagnosis step ${step}: <what>", git push -u origin ${a.branch}.`,
    '  Never commit a change to production code: the diagnosis only reads and proves.',
  ].join('\n')
}

const prompt = (step, task, rules) =>
  `${CONTEXT}\n\nStep ${step}/${STEPS.length}: ${STEPS[step - 1]}.\n${task}\n${answersText()}\n${rules ? stateRules(step, step + 1) : ''}`

function stop(step, r) {
  const base = { step, step_name: STEPS[step - 1], summary: (r && r.summary) || '' }
  if (!r) return { status: 'failed', ...base, summary: 'the step\'s agent did not return' }
  if (r.status === 'question') return { status: 'needs_input', ...base, question: r.question || '' }
  return { status: 'failed', ...base }
}
const ok = r => r && r.status === 'done'

// --- 1. Reproduce -------------------------------------------------------------
if (START <= 1) {
  const r = await agent(prompt(1,
    `Write the smallest reproduction of the symptom as a pytest test in ${DIR}/test_reproduction.py ` +
    '(it lives outside tests/ on purpose, so the suite stays green until the fix round adopts it). ' +
    `Run it with pytest ${DIR}/test_reproduction.py and confirm it FAILS for the reported reason, not some other one. ` +
    'Record the exact command and the failing output in log.md. If you cannot make it fail, try the obvious variations ' +
    'of the input; if it still passes, ask (status question) for the missing detail - never invent a failure.', true),
    { agentType: 'reproducer', model: 'sonnet', effort: 'medium', schema: RESULT, phase: 'Reproduce', label: 'reproduce' })
  if (r && r.summary) log(`reproduce: ${r.summary.split('\n')[0]}`)
  if (!ok(r)) return stop(1, r)
}

// --- 2. Hunt ------------------------------------------------------------------
let candidates = []
if (START <= 2) {
  const hunts = await parallel(Object.entries(LENSES).map(([lens, how]) => () =>
    agent(prompt(2,
      `Read-only. Cause hunter, lens "${lens}". The reproduction is ${DIR}/test_reproduction.py (log.md has how it fails). ` +
      `${how} Return at most three candidate causes, best first, each with its evidence. Run code only from a scratch ` +
      'directory or in ways that write nothing to the repository.', false),
      { agentType: 'cause-hunter', model: 'sonnet', effort: 'medium', schema: CANDIDATES, phase: 'Hunt', label: `hunt: ${lens}` })
      .then(h => h && h.candidates.map(c => ({ ...c, lens })))))
  candidates = hunts.filter(Boolean).flat()
  const missing = Object.keys(LENSES).length - hunts.filter(Boolean).length
  log(`hunt: ${candidates.length} candidate causes${missing ? `, ${missing} hunter(s) returned nothing` : ''}`)
  if (candidates.length === 0) {
    return stop(2, { status: 'fail', summary: 'no cause hunter returned a candidate' })
  }
}

// --- 3 and 4. Root cause and Critique (one revision if the critic objects) ----
const candidateText = candidates.length
  ? candidates.map(c => `- [${c.lens}, ${c.confidence}] ${c.cause}\n  evidence: ${c.evidence}`).join('\n')
  : '(from an earlier run: see "## 2. Hunt" in log.md)'

let verdict = null
let critique = null
const findingList = fs => fs.map(f => `- [${f.severity}] ${f.issue}${f.check ? ` (check: ${f.check})` : ''}`).join('\n')
// On a resume at step 3 after a dispute, the answers say which reading to follow and log.md
// holds the recorded critique: the analyst revises against both.
const resumedRevision = START === 3 && ANSWERS.some(x => x.step === 3)
for (let pass = 1; pass <= 2; pass++) {
  if (START <= 3 || pass > 1) {
    const revise = critique
      ? '\nREVISION: the critic objected. First append "## 4. Critique (pass 1)" to log.md with the findings below and ' +
        'commit, so the objections survive a restart. Then answer every finding - revise the cause, or show with ' +
        `evidence why it stands - and update the Defect block.\n${findingList(critique.findings)}`
      : resumedRevision
        ? '\nREVISION: a critique is recorded in log.md and the user has answered which reading to follow (answers ' +
          'above). Revise the Defect block to follow that answer and say how each recorded finding is settled.'
        : ''
    verdict = await agent(prompt(3,
      (pass === 1 && START <= 2
        ? 'First set state.json "step" to 3 and append "## 2. Hunt" to log.md with the candidates below; commit.\n'
        : '') +
      'Decide the root cause. Weigh the candidates, then PROVE the one you pick: make the smallest change at the cause, ' +
      `run ${DIR}/test_reproduction.py and see it pass, then REVERT the change (git checkout -- <files>; git status must ` +
      'show no production change) and say so. A cause you cannot prove that way is not the root cause.\n' +
      'Then size the class: every other input, caller or site the same cause breaks (grep for the same pattern, the same ' +
      'call, the same assumption). Decide is_bug: false only when the code does what it documents and the user expected ' +
      'something else. Write the Defect block into log.md under "## 3. Root cause" (observed, expected, reproduction, ' +
      'root cause, proof, class, scope options with what each changes and costs, recommended scope, bump) and return it.' +
      `${revise}\nCandidates:\n${candidateText}`, true),
      { agentType: 'root-cause-analyst', model: 'opus', effort: 'high', schema: VERDICT, phase: 'Root cause', label: pass === 1 ? 'root cause' : 'revise' })
    if (verdict && verdict.summary) log(`root cause: ${verdict.summary.split('\n')[0]}`)
    if (!ok(verdict)) return stop(3, verdict)
  }

  critique = await agent(prompt(4,
    'Read-only. Try to falsify the diagnosis in log.md ("## 3. Root cause"). Re-run the reproduction yourself. Look for a ' +
    'second explanation that fits the evidence as well; check the fix site is the cause and not just where the symptom ' +
    'shows; check the class is complete (one more caller, input or site with the same cause is a blocking finding); check ' +
    'is_bug is right. Mark blocking only what would change the fix. Run code only from a scratch directory.', false),
    { agentType: 'diagnosis-critic', model: 'opus', effort: 'high', schema: CRITIQUE, phase: 'Critique', label: `critique ${pass}` })
  if (!critique) return stop(4, null)
  // One signal decides: any blocking finding. `holds` is the critic's summary of the same.
  const blocking = critique.findings.filter(f => f.severity === 'blocking')
  log(`critique ${pass}: ${blocking.length === 0 ? 'holds' : `${blocking.length} blocking`}`)
  if (blocking.length === 0) break
  critique = { ...critique, findings: blocking }
  if (pass === 2) {
    // Asked at step 3, so the resume revises the Defect block with the answer before the critic reads it again.
    return stop(3, {
      status: 'question',
      summary: 'the critic still disputes the diagnosis after one revision',
      question: 'The diagnosis and its critic disagree after one revision. Record these findings under ' +
        '"## 4. Critique (pass 2)" in diagnosis/log.md before asking:\n' + findingList(blocking) +
        '\nWhich reading should the fix follow?',
    })
  }
}

// Record the critique and close the diagnosis; read the Defect block back when this run did not write it.
const record = await agent(prompt(4,
  'Record the outcome: append "## 4. Critique" to log.md with the critic\'s findings below and how each was settled, then ' +
  'follow the state rules. Return the Defect block as it now stands in log.md ("## 3. Root cause"), with is_bug.\n' +
  `Findings:\n${critique && critique.findings.length ? findingList(critique.findings) : '(none)'}`, true),
  { agentType: 'root-cause-analyst', model: 'sonnet', effort: 'low', schema: VERDICT, phase: 'Critique', label: 'record' })
if (!ok(record)) return stop(4, record)

const final = verdict || record
if (typeof final.is_bug !== 'boolean' || !final.defect) {
  return stop(4, { status: 'fail', summary: 'the diagnosis finished without a readable Defect block' })
}
return { status: 'diagnosed', is_bug: final.is_bug, summary: final.summary, defect: final.defect }
