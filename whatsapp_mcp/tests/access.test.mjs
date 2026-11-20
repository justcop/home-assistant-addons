import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { verifyAccessJwt } from '../run.mjs';

const { privateKey, publicKey } = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 });
const jwk = { ...publicKey.export({ format: 'jwk' }), kid: 'test-key', use: 'sig' };
const keys = { keys: [jwk] };
const opts = {
  access_team_domain: 'example.cloudflareaccess.com',
  access_audience: 'my-app-audience',
  allowed_email: 'me@example.com'
};
const now = Date.UTC(2026, 10, 20);
const seconds = Math.floor(now / 1000);
const claims = {
  iss: 'https://example.cloudflareaccess.com',
  aud: ['my-app-audience'],
  email: 'me@example.com',
  iat: seconds - 5,
  exp: seconds + 900
};

function token(payload = claims, key = privateKey, header = { alg: 'RS256', kid: 'test-key' }) {
  const signing = [header, payload]
    .map(part => Buffer.from(JSON.stringify(part)).toString('base64url')).join('.');
  return signing + '.' + crypto.sign('RSA-SHA256', Buffer.from(signing), key).toString('base64url');
}

test('accepts a signed Cloudflare assertion for the configured email and audience', () => {
  assert.equal(verifyAccessJwt(token(), opts, keys, now), true);
});
test('rejects wrong email, issuer, audience and expiry', () => {
  for (const mutation of [
    { email: 'stranger@example.com' },
    { iss: 'https://other.cloudflareaccess.com' },
    { aud: ['another-app'] },
    { exp: seconds - 1 },
    { iat: seconds + 100 }
  ]) assert.equal(verifyAccessJwt(token({ ...claims, ...mutation }), opts, keys, now), false);
});
test('rejects missing, damaged and forged assertions', () => {
  const rogueKey = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 }).privateKey;
  for (const item of [
    null, '', 'nonsense', token().slice(0, -5), token(claims, rogueKey),
    token(claims, privateKey, { alg: 'none', kid: 'test-key' }),
    token(claims, privateKey, { alg: 'RS256', kid: 'unknown-key' })
  ]) assert.equal(verifyAccessJwt(item, opts, keys, now), false);
});
test('rejects assertions without verified timestamps', () => {
  assert.equal(verifyAccessJwt(token({ ...claims, exp: undefined }), opts, keys, now), false);
  assert.equal(verifyAccessJwt(token({ ...claims, nbf: seconds + 500 }), opts, keys, now), false);
});
