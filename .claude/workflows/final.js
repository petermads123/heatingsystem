export const meta = {
  name: 'final',
  description: 'Whole-feature check before the pull request: test gate, then three concept checkers and two extras scouts',
  whenToUse: 'Started by the /finish skill once every round has merged, never directly.',
  phases: [
    { title: 'Test gate', detail: 'full suite, ruff, mypy, check_sync', model: 'sonnet' },
    { title: 'Concept check', detail: 'three independent full checks against BRIEF.md', model: 'sonnet' },
    { title: 'Extras', detail: 'critical gaps and highest-value follow-ups', model: 'sonnet' },
  ],
}

// args (from /finish): { feature_dir, feature_branch, kind }   kind "fix" adds the defect-class scout
// Read-only: nothing here writes, commits or pushes. /finish acts on the result.
// Returns { tests, checks: [{checker, pass, summary, findings}], extras: [...], missing }

const a = args || {}
for (const key of ['feature_dir', 'feature_branch']) {
  if (!a[key]) throw new Error(`final workflow: missing args.${key}`)
}

const CONTEXT =
  `The feature in ${a.feature_dir}/ (BRIEF.md, brief.json, one folder per round with log.md) is fully built on ` +
  `${a.feature_branch}, which is checked out. Read-only: change no file, make no commit.`

const FINDING = {
  type: 'object',
  properties: {
    severity: { type: 'string', enum: ['blocking', 'minor'] },
    where: { type: 'string' },
    issue: { type: 'string' },
    fix: { type: 'string' },
  },
  required: ['severity', 'where', 'issue'],
}
const TEST_RUN = {
  type: 'object',
  properties: {
    passed: { type: 'boolean' },
    summary: { type: 'string' },
    failures: { type: 'array', items: { type: 'string' } },
  },
  required: ['passed', 'summary', 'failures'],
}
const VERDICT = {
  type: 'object',
  properties: {
    pass: { type: 'boolean' },
    summary: { type: 'string' },
    findings: { type: 'array', items: FINDING },
  },
  required: ['pass', 'summary', 'findings'],
}
const EXTRAS = {
  type: 'object',
  properties: {
    extras: {
      type: 'array',
      maxItems: 5,
      items: {
        type: 'object',
        properties: {
          title: { type: 'string' },
          why: { type: 'string', description: 'The value it adds or the harm it prevents, concretely' },
          critical: { type: 'boolean', description: 'True only if shipping without it would be wrong: data loss, a broken promise of the BRIEF, a security hole, a crash on ordinary input' },
          size: { type: 'string', enum: ['small', 'round', 'feature'] },
        },
        required: ['title', 'why', 'critical', 'size'],
      },
    },
  },
  required: ['extras'],
}

const SCOUTS = {
  critical: 'Look for what would make shipping this feature wrong: unhandled ordinary inputs, broken promises of the BRIEF, security or data-loss risks. Report nothing rather than something minor.',
  value: 'Look for the few follow-ups that would add the most value to this feature for its users. Only the best two or three; none if nothing stands out.',
}
if (a.kind === 'fix') {
  SCOUTS['defect-class'] = 'This is a fix (BRIEF.md has the Defect block). Look for the same cause elsewhere that the ' +
    'chosen scope left out, and for what let this defect ship (a missing test, an unchecked input, a misleading ' +
    'docstring). Critical only if a site outside the scope is broken by the same cause in ordinary use.'
}
const CHECKERS = 3

// The test gate runs first and alone: the checkers and scouts may run code, and a second
// mypy or pytest in the same tree at the same time can fail for reasons of its own.
phase('Test gate')
const tests = await agent(
  `${CONTEXT}\nRun pytest (whole suite), ruff check, ruff format --check, mypy and python .claude/hooks/check_sync.py ` +
  '(the last one may report STRUCTURE.md, README and DEVLOG drift: that is expected before /finish writes them; ' +
  'report it separately in the summary and do not count it as a failure). Report exactly what fails.',
  { agentType: 'test-runner', model: 'sonnet', effort: 'low', schema: TEST_RUN, phase: 'Test gate', label: 'full gate' })
const gate = tests ? `The test gate already ran: ${tests.passed ? 'green' : 'RED'}. ${tests.summary}` : 'The test gate did not report.'

const checksP = parallel(Array.from({ length: CHECKERS }, (_, i) => () =>
  agent(`${CONTEXT}\n${gate} Do not run mypy or the whole suite again; run the code and single tests as you need.\n` +
    `You are feature concept checker ${i + 1} of ${CHECKERS}; the others check the same thing independently. ` +
    'Do a full check of the whole feature against BRIEF.md:\n' +
    '- Concept: does the feature as built match the BRIEF - every round delivered, the rounds fitting together, ' +
    'nothing half-done, nothing outside the BRIEF shipped by accident?\n' +
    '- Purpose: does it serve the goal, used the way a caller would use it? Run it.\n' +
    '- Acceptance criteria: is every feature-level criterion (F1, F2, ...) met? Cite the code and the test for each.\n' +
    (a.kind === 'fix' ? '- Fix: is the change at the root cause in the Defect block, with the reproduction now in tests/ and green?\n' : '') +
    'pass=false only for a real gap; mark those findings blocking.',
    { agentType: 'concept-checker', model: 'sonnet', effort: 'low', schema: VERDICT, phase: 'Concept check', label: `check ${i + 1}` })
    .then(v => v && { checker: i + 1, ...v })))

const extrasP = parallel(Object.entries(SCOUTS).map(([lens, task]) => () =>
  agent(`${CONTEXT}\n${gate}\nExtras scout, lens "${lens}": ${task}`,
    { agentType: 'extras-scout', model: 'sonnet', effort: 'medium', schema: EXTRAS, phase: 'Extras', label: `extras: ${lens}` })
    .then(r => r && { lens, extras: r.extras.map(x => ({ ...x, lens })) })))

const [checks, scouts] = await Promise.all([checksP, extrasP])
const verdicts = checks.filter(Boolean)
const scoutsBack = scouts.filter(Boolean)
// Nothing missing may read as a pass: /finish holds the pull request while anything here is missing.
const missing = {
  tests: !tests,
  checks: CHECKERS - verdicts.length,
  scouts: Object.keys(SCOUTS).filter(l => !scoutsBack.some(s => s.lens === l)),
}
log(`tests ${tests ? (tests.passed ? 'green' : 'red') : 'missing'}; concept ${verdicts.filter(v => v.pass).length}/${verdicts.length} pass`)
return { tests, checks: verdicts, extras: scoutsBack.flatMap(s => s.extras), missing }
