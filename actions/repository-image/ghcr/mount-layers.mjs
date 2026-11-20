import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const GHCR = 'https://ghcr.io';
const SOURCE = 'rubio-enterprises/base';
const DIGEST = /^sha256:[a-f0-9]{64}$/;

function layerDigests(manifest) {
  if (!Array.isArray(manifest.layers) || manifest.layers.some(layer => typeof layer?.digest !== 'string' || !DIGEST.test(layer.digest))) {
    throw new Error('Expected a single-platform image manifest with valid layer digests');
  }
  return new Set(manifest.layers.map(layer => layer.digest));
}

export async function mountBaseLayers({ repository, base, image, actor, password, origin = GHCR }) {
  const layers = [...layerDigests(image).intersection(layerDigests(base))];
  if (!layers.length) return;

  const tokenUrl = new URL('/token', origin);
  tokenUrl.search = new URLSearchParams([
    ['service', 'ghcr.io'],
    ['scope', `repository:${repository}:pull,push`],
    ['scope', `repository:${SOURCE}:pull`],
  ]);
  const request = (url, method, authorization) => fetch(url, {
    method, headers: { authorization }, redirect: 'manual', signal: AbortSignal.timeout(20_000),
  });
  const basic = Buffer.from(`${actor}:${password}`).toString('base64');
  const auth = await request(tokenUrl, 'GET', `Basic ${basic}`);
  if (!auth.ok) throw new Error(`GHCR token service returned HTTP ${auth.status}`);
  const { token, access_token: accessToken } = await auth.json().catch(() => {
    throw new Error('GHCR token service returned invalid JSON');
  });
  const bearer = token || accessToken;
  if (typeof bearer !== 'string' || !bearer) throw new Error('GHCR token service returned no token');

  const blobs = new URL(`/v2/${repository}/blobs/`, origin);
  let mounted = 0;
  for (const digest of layers) {
    const status = async (url, method) => {
      try {
        return (await request(url, method, `Bearer ${bearer}`)).status;
      } catch {
        throw new Error(`GHCR ${method} failed for base layer ${digest}`);
      }
    };
    const head = await status(new URL(`./${digest}`, blobs), 'HEAD');
    if (head === 200) continue;
    if (head !== 404) throw new Error(`GHCR could not check base layer ${digest} (HTTP ${head})`);

    const upload = new URL('uploads/', blobs);
    upload.search = new URLSearchParams({ mount: digest, from: SOURCE });
    const mount = await status(upload, 'POST');
    if (mount !== 201) throw new Error(`GHCR refused to mount base layer ${digest} from ${SOURCE} (HTTP ${mount})`);
    mounted++;
  }
  console.log(`GHCR base layers: ${mounted} mounted, ${layers.length - mounted} already present`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [target, baseManifest, imageManifest] = process.argv.slice(2);
  try {
    if (!/^ghcr\.io\/[a-z0-9._-]+(?:\/[a-z0-9._-]+)+$/.test(target) || target.split('/').some(part => part === '.' || part === '..')) {
      throw new Error('Expected an untagged GHCR destination');
    }
    const parse = file => JSON.parse(readFileSync(file, 'utf8'));
    await mountBaseLayers({
      repository: target.slice('ghcr.io/'.length), base: parse(baseManifest), image: parse(imageManifest),
      actor: process.env.GITHUB_ACTOR, password: process.env.REGISTRY_TOKEN,
    });
  } catch (error) {
    console.error(`::error::${error.message}`);
    process.exitCode = 1;
  }
}
