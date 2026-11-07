/**
 * Secret box — symmetric encryption for the one secret the API stores
 * in the DB: the static.llm_config API key (the Config → LLM tab).
 *
 * AES-256-GCM via node crypto; the master key is the 32-byte
 * LLM_SECRET_KEY from database/.env (64 hex chars — generate with
 * `openssl rand -hex 32`). Storage format (the SAME bytes Python's
 * cryptography AESGCM produces, so llm_agents/deep_agents/llm_config.py
 * decrypts without any cross-language KDF):
 *
 *   v1:<base64 12-byte IV>.<base64 ciphertext||tag>
 *
 * (Python's AESGCM.encrypt returns ciphertext||tag as one buffer, so
 * the tag rides the payload's tail on both sides.)
 *
 * Plaintext exists ONLY inside the calling request: the API never
 * selects the stored ciphertext back out, never echoes a plaintext key
 * in a response, and never logs one (errors carry no key material).
 * What any read returns is the masked hint (secretHint) the write
 * stored alongside the ciphertext.
 */
import crypto from "crypto";
import fs from "fs";
import path from "path";
import dotenv from "dotenv";

const FORMAT_VERSION = "v1";
const IV_BYTES = 12;
const AUTH_TAG_BYTES = 16;
const KEY_HEX_RE = /^[0-9a-fA-F]{64}$/;

// database/.env is the single source of truth for LLM_SECRET_KEY — the
// same load contract as db.service (which owns the SUPABASE_* vars).
let _envLoaded = false;
function loadLlmEnv(): void {
  if (_envLoaded) return;
  const envPath = path.resolve(
    import.meta.dirname ?? __dirname,
    "..",
    "..",
    "..",
    "database",
    ".env",
  );
  if (fs.existsSync(envPath)) {
    dotenv.config({ path: envPath });
  }
  _envLoaded = true;
}

let _keyCache: Buffer | null | undefined;

/** The 32-byte master key, or null when LLM_SECRET_KEY is absent/not
 *  64 hex chars (warned ONCE — every encrypt then fails with the
 *  config message, every decrypt fails with the same cause). */
function masterKey(): Buffer | null {
  if (_keyCache !== undefined) return _keyCache;
  loadLlmEnv();
  const hex = (process.env.LLM_SECRET_KEY ?? "").trim();
  if (KEY_HEX_RE.test(hex)) {
    _keyCache = Buffer.from(hex, "hex");
  } else {
    _keyCache = null;
    console.warn(
      "[secret-box] LLM_SECRET_KEY missing or not 64 hex chars in database/.env — " +
        "api-key encryption unavailable (generate one: openssl rand -hex 32)",
    );
  }
  return _keyCache;
}

/** True when a usable master key is configured (the routes surface
 *  this as a 400 with the setup hint instead of an opaque failure). */
export function secretBoxConfigured(): boolean {
  return masterKey() != null;
}

/** Encrypt a plaintext secret into the storable `v1:…` blob. Throws
 *  (config message) when no master key is set. */
export function encryptSecret(plaintext: string): string {
  const key = masterKey();
  if (key == null) {
    throw new Error(
      "LLM_SECRET_KEY is not configured in database/.env — cannot store the API key",
    );
  }
  const iv = crypto.randomBytes(IV_BYTES);
  const cipher = crypto.createCipheriv("aes-256-gcm", key, iv);
  const ciphertext = Buffer.concat([cipher.update(plaintext, "utf-8"), cipher.final()]);
  const blob = Buffer.concat([ciphertext, cipher.getAuthTag()]);
  return `${FORMAT_VERSION}:${iv.toString("base64")}.${blob.toString("base64")}`;
}

/** Decrypt a `v1:…` blob back to plaintext (the Python side shares the
 *  format; Node itself never needs this on the API path). Throws on an
 *  unrecognized format or an auth-tag mismatch — a wrong master key or
 *  a tampered blob can never yield bytes. */
export function decryptSecret(blob: string): string {
  const key = masterKey();
  if (key == null) {
    throw new Error("LLM_SECRET_KEY is not configured in database/.env — cannot decrypt");
  }
  const [version, payload, ...extraParts] = blob.trim().split(":");
  if (version !== FORMAT_VERSION || extraParts.length > 0 || payload == null) {
    throw new Error("unrecognized secret blob format");
  }
  const [ivB64, dataB64, ...extraSegs] = payload.split(".");
  if (!ivB64 || !dataB64 || extraSegs.length > 0) {
    throw new Error("unrecognized secret blob format");
  }
  const iv = Buffer.from(ivB64, "base64");
  const data = Buffer.from(dataB64, "base64");
  if (data.length <= AUTH_TAG_BYTES) {
    throw new Error("secret blob too short");
  }
  const tag = data.subarray(data.length - AUTH_TAG_BYTES);
  const ciphertext = data.subarray(0, data.length - AUTH_TAG_BYTES);
  const decipher = crypto.createDecipheriv("aes-256-gcm", key, iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(ciphertext), decipher.final()]).toString("utf-8");
}

/** The masked preview stored alongside the ciphertext — everything a
 *  read ever surfaces: fixed dot run + the last 4 characters. */
export function secretHint(plaintext: string): string {
  const tail = plaintext.slice(-4);
  return `••••••••${tail}`;
}
