import { createModels, createProvider } from '@earendil-works/pi-ai';
import { openAICompletionsApi } from '@earendil-works/pi-ai/api/openai-completions.lazy';
import { openAIResponsesApi } from '@earendil-works/pi-ai/api/openai-responses.lazy';

// Fixed providers only. Neither source text nor tool arguments can set an endpoint.
export function publicationProvider(input) {
  const baseUrl = input.provider === 'openai' ? 'https://api.openai.com/v1' : 'https://openrouter.ai/api/v1';
  const model = {
    id: input.model, name: input.model, api: input.provider === 'openai' ? 'openai-responses' : 'openai-completions',
    provider: input.provider, baseUrl, reasoning: input.provider === 'openai', input: ['text'],
    contextWindow: 128000, maxTokens: 2500,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
  };
  const models = createModels();
  models.setProvider(createProvider({
    id: input.provider, name: input.provider, baseUrl, models: [model],
    api: input.provider === 'openai' ? openAIResponsesApi() : openAICompletionsApi(),
    auth: { apiKey: { name: 'Quib27 supplied key', resolve: async () => ({ auth: { apiKey: input.api_key } }) } },
  }));
  return { model, streamFn: (currentModel, context, options) => models.streamSimple(currentModel, context, {
    ...options, apiKey: input.api_key, maxTokens: 2500,
  }) };
}
