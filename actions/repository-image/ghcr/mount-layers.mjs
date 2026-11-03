#!/usr/bin/env node
// Mounts the layers of one image that already exist in another package of the same registry, so the copy that follows
// uploads only new content. `crane copy` mounts only within one registry and the copy here starts at the staging
// registry, so every layer GHCR already holds (base, base-ci, and the dev image's own layers for the CI image) would be
// sent again over the uplink. After a mount, crane's existence check finds the blob and skips it.
//
//   mount-layers.mjs <target-image> <target-manifest.json> [<source-image>=<source-manifest.json> ...]
//
// A manifest file is a single-platform image manifest. The first source holding a layer wins. A source on another
// registry is ignored. Needs GITHUB_ACTOR and REGISTRY_TOKEN, and sends them only to the registry's own token service.
// This lives in a subdirectory on purpose: the image input key hashes only the files directly in actions/repository-image/.
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';

const DIGEST = /^sha256:[a-f0-9]{64}$/;
const CONCURRENCY = 8;

export function parseImage(image) {
  const slash = image.indexOf('/');
  if (slash < 1) throw new Error(`not a registry image: ${image}`);
  const host = image.slice(0, slash);
  let repository = image.slice(slash + 1).replace(/@.*$/, '');
  const colon = repository.lastIndexOf(':');
  if (colon > repository.lastIndexOf('/')) repository = repository.slice(0, colon);
  if (!repository) throw new Error(`not a registry image: ${image}`);
  return { host, repository };
}

const readLayers = file => {
  const manifest = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (!Array.isArray(manifest.layers)) throw new Error(`${file} is not an image manifest`);
  return manifest.layers.map(({ digest, size }) => {
    if (!DIGEST.test(digest)) throw new Error(`${file} has an invalid layer digest`);
    return { digest, size: Number(size) || 0 };
  });
};

const megabytes = bytes => `${(bytes / 1e6).toFixed(1)} MB`;

export async function mountLayers({ target, targetManifest, sources, actor, password, fetchImpl = fetch, log = console.log }) {
  const destination = parseImage(target);
  const origin = `${/^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$/.test(destination.host) ? 'http' : 'https'}://${destination.host}`;
  const owners = new Map();
  for (const { image, manifest } of sources) {
    const source = parseImage(image);
    if (source.host !== destination.host) {
      log(`Skipping mount source ${image}: not on ${destination.host}`);
      continue;
    }
    if (source.repository === destination.repository) continue;
    for (const { digest } of readLayers(manifest)) if (!owners.has(digest)) owners.set(digest, source.repository);
  }
  const scopes = [`repository:${destination.repository}:push,pull`, ...new Set([...owners.values()].map(name => `repository:${name}:pull`))];

  let token;
  const authenticate = async challenge => {
    const parameters = Object.fromEntries([...challenge.matchAll(/(\w+)="([^"]*)"/g)].map(match => [match[1], match[2]]));
    if (!/^Bearer /i.test(challenge) || !parameters.realm) throw new Error('the registry did not offer a bearer token service');
    const realm = new URL(parameters.realm);
    if (realm.origin !== origin) throw new Error(`refusing to send credentials to ${realm.origin}`);
    if (parameters.service) realm.searchParams.set('service', parameters.service);
    for (const scope of scopes) realm.searchParams.append('scope', scope);
    const response = await fetchImpl(realm, { headers: { authorization: `Basic ${Buffer.from(`${actor}:${password}`).toString('base64')}` } });
    if (!response.ok) throw new Error(`the token service answered ${response.status}`);
    const body = await response.json();
    token = body.token || body.access_token;
    if (!token) throw new Error('the token service returned no token');
    log(`::add-mask::${token}`);
  };
  const request = async (method, path) => {
    for (let attempt = 0; ; attempt++) {
      const response = await fetchImpl(`${origin}${path}`, { method, headers: token ? { authorization: `Bearer ${token}` } : {} });
      if (response.status !== 401 || attempt > 0) return response;
      await authenticate(response.headers.get('www-authenticate') || '');
    }
  };

  const layers = [...new Map(readLayers(targetManifest).map(layer => [layer.digest, layer])).values()];
  const totals = { mounted: 0, existing: 0, refused: 0, upload: 0 };
  const bytes = { mounted: 0, existing: 0, refused: 0, upload: 0 };
  const count = (kind, layer) => { totals[kind]++; bytes[kind] += layer.size; };
  const queue = [...layers];
  const worker = async () => {
    for (let layer = queue.shift(); layer; layer = queue.shift()) {
      const from = owners.get(layer.digest);
      if (!from) { count('upload', layer); continue; }
      const blob = `/v2/${destination.repository}/blobs/${layer.digest}`;
      if ((await request('HEAD', blob)).status === 200) { count('existing', layer); continue; }
      // GHCR answers 202, and leaves an empty upload session, when it will not mount (no `from`, or one it cannot read)
      const mount = await request('POST', `/v2/${destination.repository}/blobs/uploads/?mount=${layer.digest}&from=${encodeURIComponent(from)}`);
      if (mount.status === 201) {
        count('mounted', layer);
        log(`Mounted ${layer.digest} (${megabytes(layer.size)}) from ${from}`);
      } else {
        count('refused', layer);
        log(`::warning::GHCR did not mount ${layer.digest} from ${from} (${mount.status}); it will be uploaded`);
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, layers.length) }, worker));
  log(`${target}: ${totals.mounted} layers mounted (${megabytes(bytes.mounted)}), ${totals.existing} already present ` +
    `(${megabytes(bytes.existing)}), ${totals.refused + totals.upload} to upload (${megabytes(bytes.refused + bytes.upload)})`);
  return { totals, bytes };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [target, targetManifest, ...rest] = process.argv.slice(2);
  const sources = rest.map(argument => {
    const split = argument.lastIndexOf('=');
    if (split < 1) { console.error(`::error::mount source must be <image>=<manifest.json>: ${argument}`); process.exit(2); }
    return { image: argument.slice(0, split), manifest: argument.slice(split + 1) };
  });
  const { GITHUB_ACTOR: actor, REGISTRY_TOKEN: password } = process.env;
  if (!target || !targetManifest || !actor || !password) {
    console.error('::error::usage: mount-layers.mjs <target-image> <target-manifest.json> [<source-image>=<manifest.json> ...] with GITHUB_ACTOR and REGISTRY_TOKEN');
    process.exit(2);
  }
  // Mounting only saves bytes, so a registry or network problem must not stop the publication that follows it
  await mountLayers({ target, targetManifest, sources, actor, password }).catch(error => {
    console.log(`::warning::Cross-repository mounts skipped: ${error.message}`);
  });
}
