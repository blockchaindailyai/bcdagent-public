const fs = require('node:fs');
const path = require('node:path');

const DEFAULT_EVENTS_DIR = path.join(__dirname, '..', 'data', 'indexer');
const DEFAULT_EVENTS_FILE = path.join(DEFAULT_EVENTS_DIR, 'events.jsonl');

function toInteger(raw, fallback) {
  const parsed = Number(raw);
  if (!Number.isInteger(parsed) || parsed <= 0) return fallback;
  return parsed;
}

function resolveIndexerConfig() {
  const dataDir = (process.env.MCP_TRON_INDEXER_DIR || DEFAULT_EVENTS_DIR).trim();
  const eventsFile = (process.env.MCP_TRON_INDEXER_FILE || path.join(dataDir, 'events.jsonl')).trim();
  const maxEventBytes = toInteger(process.env.MCP_TRON_INDEXER_MAX_EVENT_BYTES, 65536);
  const maxReadEvents = toInteger(process.env.MCP_TRON_INDEXER_MAX_READ_EVENTS, 200);

  return {
    dataDir,
    eventsFile,
    maxEventBytes,
    maxReadEvents,
  };
}

function ensureParentDir(filePath) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
}

function createIndexerStore(config = resolveIndexerConfig()) {
  ensureParentDir(config.eventsFile);

  function appendEvent(eventPayload) {
    const eventRecord = {
      event_id: `evt_${Date.now()}_${Math.random().toString(16).slice(2, 10)}`,
      observed_at: new Date().toISOString(),
      ...eventPayload,
    };

    const line = `${JSON.stringify(eventRecord)}\n`;
    if (Buffer.byteLength(line, 'utf8') > config.maxEventBytes) {
      throw new Error(`indexer_event_too_large:max=${config.maxEventBytes}`);
    }

    fs.appendFileSync(config.eventsFile, line, { encoding: 'utf8' });
    return eventRecord;
  }

  function readRecentEvents(limit = 50) {
    const normalizedLimit = Math.min(toInteger(limit, 50), config.maxReadEvents);
    if (!fs.existsSync(config.eventsFile)) {
      return [];
    }

    const raw = fs.readFileSync(config.eventsFile, 'utf8').trim();
    if (!raw) return [];

    const lines = raw.split('\n');
    const selected = lines.slice(-normalizedLimit);
    const out = [];

    for (const line of selected) {
      try {
        out.push(JSON.parse(line));
      } catch (_error) {
        out.push({ malformed: true, raw: line });
      }
    }

    return out;
  }

  function stats() {
    const exists = fs.existsSync(config.eventsFile);
    if (!exists) {
      return {
        events_file: config.eventsFile,
        total_events: 0,
        bytes: 0,
      };
    }

    const content = fs.readFileSync(config.eventsFile, 'utf8').trim();
    const totalEvents = content ? content.split('\n').length : 0;
    const bytes = fs.statSync(config.eventsFile).size;
    return {
      events_file: config.eventsFile,
      total_events: totalEvents,
      bytes,
    };
  }

  return {
    appendEvent,
    readRecentEvents,
    stats,
    config,
  };
}

module.exports = {
  DEFAULT_EVENTS_DIR,
  DEFAULT_EVENTS_FILE,
  createIndexerStore,
  resolveIndexerConfig,
};
