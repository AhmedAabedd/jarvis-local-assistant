# Speech integration verification to-do

Mounir must distinguish an implemented speech adapter from one that has been
verified against a live provider. Mocked unit tests alone are not sufficient to
claim that a cloud TTS or STT integration works.

## Verification states

- [ ] Expose separate states for implemented, contract-tested, live-verified,
  user-tested, and degraded integrations.
- [ ] Show the last live-verification time and sanitized failure reason.
- [ ] Never describe an adapter as live-verified based only on mocked responses.

## Automated user checks

- [ ] Add one **Test all configured speech models** action.
- [ ] Test every saved TTS and STT configuration without requiring the user to
  open and test models individually.
- [ ] Report connected, failed, and untested results per model with actionable
  errors, while allowing the remaining checks to continue after one failure.
- [ ] Persist every result and refresh all affected interface status indicators
  immediately.

## Maintainer live-provider matrix

- [ ] Run credential-backed smoke tests for representative OpenAI-compatible,
  OpenRouter, Deepgram Aura, Deepgram Flux, ElevenLabs, Google, Azure, and AWS
  TTS/STT contracts.
- [ ] Test local adapters separately from cloud adapters.
- [ ] Cover each distinct API contract or provider version; testing every voice
  is unnecessary when voices share the same contract.
- [ ] Schedule recurring live checks so upstream API changes are detected.
- [ ] Keep secrets in CI secret storage, minimize billable test payloads, and
  never expose credentials or provider responses containing private data.
- [ ] Record the provider, contract/version, representative model, result, and
  verification timestamp.

## Regression coverage

- [ ] Keep deterministic mocked tests for payload construction, endpoint
  normalization, authentication, media handling, and error sanitization.
- [ ] Add a regression test whenever a real provider rejects a request that the
  mocked suite previously accepted.
- [ ] Make provider-specific format and option limitations explicit in adapter
  metadata and the interface.

