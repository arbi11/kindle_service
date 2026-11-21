import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createModels, fauxProvider, fauxAssistantMessage, fauxToolCall, fauxText } from '@earendil-works/pi-ai';
import { chooseArticles } from './selection.mjs';

const input = {
  provider: 'openai', model: 'test-fixture', api_key: 'synthetic-test-key', maximum: 1,
  preferences: 'Patient investing. Exclude daily stock tips.',
  articles: [{ id: '0', title: 'Patient investing', description: 'A long-term view' },
    { id: '1', title: 'Today’s stock tips', description: 'Intraday trading' }],
};

function scripted(responses) {
  const faux = fauxProvider({ tokensPerSecond: 1000000 });
  const models = createModels();
  models.setProvider(faux.provider);
  faux.setResponses(responses);
  let calls = 0;
  return {
    streamFn: (_model, context, options) => {
      calls++;
      return models.streamSimple(faux.getModel(), context, options);
    },
    calls: () => calls,
  };
}
const call = (name, args) => fauxAssistantMessage([fauxToolCall(name, args)], { stopReason: 'toolUse' });

test('Pi lists articles, selects a valid ID and terminates without an extra model call', async () => {
  const fixture = scripted([call('list_articles', {}), call('select_articles', {
    selected: [{ id: '0', reason: 'Long-term investing, without intraday tips.' }],
  })]);
  const result = await chooseArticles(input, fixture);
  assert.equal(result.runtime, 'pi-agent-core');
  assert.equal(result.selected[0].id, '0');
  assert.equal(fixture.calls(), 2);
});

test('no matching articles is a successful empty result', async () => {
  const fixture = scripted([call('list_articles', {}), call('select_articles', { selected: [] })]);
  const result = await chooseArticles({ ...input, preferences: 'Only gardening' }, fixture);
  assert.deepEqual(result.selected, []);
});

test('a run that never selects stops after three model turns', async () => {
  const fixture = scripted([1, 2, 3].map(() => fauxAssistantMessage([fauxText('Still thinking.')])));
  await assert.rejects(chooseArticles(input, fixture), /did not finish/);
  assert.ok(fixture.calls() <= 3);
});

test('invalid IDs are rejected by Pi tool validation and can be corrected', async () => {
  const fixture = scripted([call('select_articles', { selected: [{ id: 'invented', reason: 'Invalid' }] }),
    call('select_articles', { selected: [{ id: '0', reason: 'A supplied ID' }] })]);
  const result = await chooseArticles(input, fixture);
  assert.equal(result.selected[0].id, '0');
});
