const test = require('node:test')
const assert = require('node:assert/strict')

const panel = require('../static/pipeline-panel.js')

function runStarted(runId = 'run-1') {
  return {
    schema_version: 1,
    action: 'run_started',
    run_id: runId,
    sequence: 1,
    graph_version: 'new',
    started_at_ms: 1000,
    phase_catalog: [{ key: 'components', label: 'Components', order: 1 }],
    stage_catalog: [{ key: 'select', label: 'Select components', phase: 'components', order: 1 }],
  }
}

test('reducer tracks stage attempts and terminal state', () => {
  let state = panel.reducePipelineEvent(panel.createInitialState(), runStarted())
  state = panel.reducePipelineEvent(state, {
    schema_version: 1,
    action: 'stage_started',
    run_id: 'run-1',
    sequence: 2,
    stage_key: 'select',
    stage_label: 'Select components',
    phase: 'components',
    order: 1,
    attempt: 1,
    status: 'running',
    started_at_ms: 1100,
  })
  state = panel.reducePipelineEvent(state, {
    schema_version: 1,
    action: 'stage_finished',
    run_id: 'run-1',
    sequence: 3,
    stage_key: 'select',
    stage_label: 'Select components',
    phase: 'components',
    order: 1,
    attempt: 1,
    status: 'completed',
    started_at_ms: 1100,
    completed_at_ms: 1500,
    duration_ms: 400,
    summary: '3 components retained',
    metrics: { components: 3 },
  })

  assert.equal(state.stages.select[0].status, 'completed')
  assert.equal(state.stages.select[0].metrics.components, 3)
  assert.equal(state.currentStage, null)
})

test('new run_started replaces the previous run', () => {
  let state = panel.reducePipelineEvent(panel.createInitialState(), runStarted('old-run'))
  state = panel.reducePipelineEvent(state, runStarted('new-run'))

  assert.equal(state.runId, 'new-run')
  assert.equal(state.runStatus, 'running')
})

test('stale and duplicate events are ignored', () => {
  const initial = panel.reducePipelineEvent(panel.createInitialState(), runStarted())
  const duplicate = panel.reducePipelineEvent(initial, { ...runStarted(), action: 'run_finished' })
  const otherRun = panel.reducePipelineEvent(initial, {
    schema_version: 1,
    action: 'run_finished',
    run_id: 'other-run',
    sequence: 2,
    status: 'failed',
  })

  assert.equal(duplicate, initial)
  assert.equal(otherRun, initial)
})

test('snapshot hydration restores attempts without replaying events', () => {
  panel.hydratePipeline({
    schema_version: 1,
    run_id: 'snapshot-run',
    graph_version: 'new',
    status: 'waiting',
    sequence: 8,
    started_at_ms: 1000,
    current_stage: 'select',
    current_attempt: 2,
    phase_catalog: [{ key: 'components', label: 'Components', order: 1 }],
    stage_catalog: [{ key: 'select', label: 'Select components', phase: 'components', order: 1 }],
    stages: {
      select: [
        { attempt: 1, status: 'warning', started_at_ms: 1000, duration_ms: 100 },
        { attempt: 2, status: 'waiting', started_at_ms: 1200, duration_ms: null },
      ],
    },
  })

  const state = panel.getState()
  assert.equal(state.runId, 'snapshot-run')
  assert.equal(state.stages.select.length, 2)
  assert.equal(state.currentAttempt, 2)
})

test('attaching the same socket twice does not duplicate listeners', () => {
  const listeners = []
  const socket = {
    on(event, handler) { listeners.push([event, handler]) },
    off() {},
  }

  panel.attachSocket(socket)
  panel.attachSocket(socket)

  assert.equal(listeners.length, 4)
})
