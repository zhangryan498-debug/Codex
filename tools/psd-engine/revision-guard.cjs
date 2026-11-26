'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const VERSION = /^v(0|[1-9]\d{0,8})$/;
const WORK_KEYS = ['id', 'task_id', 'sku', 'site', 'platform', 'country', 'scope'];

function readJSON(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
}

function digest(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}

function canonical(value) {
  return JSON.stringify(value, (_, entry) =>
    entry && typeof entry === 'object' && !Array.isArray(entry)
      ? Object.fromEntries(Object.keys(entry).sort().map(key => [key, entry[key]]))
      : entry);
}

function samePath(a, b) {
  return process.platform === 'win32' ? a.toLowerCase() === b.toLowerCase() : a === b;
}

function latestEntry(root) {
  return fs.readdirSync(root, {withFileTypes: true})
    .filter(entry => VERSION.test(entry.name))
    .sort((a, b) => Number(b.name.slice(1)) - Number(a.name.slice(1)))[0];
}

function readCompletedVersion(root, entry) {
  const dir = path.join(root, entry.name);
  try {
    if (!entry.isDirectory() || !samePath(fs.realpathSync(dir), dir)) {
      throw Error('version directory is not a physical child');
    }
    const layout = readJSON(path.join(dir, 'layout.json'));
    const manifestFile = path.join(dir, 'manifest.json');
    const manifest = readJSON(manifestFile);
    if (manifest.managedRevision) {
      const completion = readJSON(path.join(dir, 'revision-complete.json'));
      const checkFile = path.join(dir, 'edit-check.json');
      if (manifest.managedRevision.version !== entry.name || completion.version !== entry.name ||
          completion.manifest_sha256 !== digest(fs.readFileSync(manifestFile)) ||
          completion.edit_check_sha256 !== digest(fs.readFileSync(checkFile)) ||
          !readJSON(checkFile).pass) {
        throw Error('managed revision did not complete its edit checks');
      }
    }
    if (manifest.layoutId !== layout.id || manifest.layout_sha256 !== digest(canonical(layout)) ||
        manifest.original_png_sha256 !== digest(fs.readFileSync(path.join(dir, 'original.png'))) ||
        manifest.psd?.status !== 'EXPORTED_AND_READ_BACK' ||
        manifest.psd.sha256 !== digest(fs.readFileSync(path.join(dir, 'layered.psd')))) {
      throw Error('layout, PNG, or layered PSD failed integrity verification');
    }
    return {dir, layout, manifest};
  } catch (error) {
    throw Error('CURRENT_REVISION_UNAVAILABLE: ' + entry.name + ' (' + dir + '): ' + error.message);
  }
}

async function withCurrentRevision({base, out, newLayoutPath, layout, baseline}, edit) {
  const baseDir = fs.realpathSync(base);
  const root = path.dirname(baseDir);
  const requested = path.basename(baseDir);
  if (!VERSION.test(requested) || !samePath(path.resolve(base), baseDir)) {
    throw Error('VERSIONED_BASE_REQUIRED: use the actual <work>/vN/layout.json in its existing revision family');
  }
  const lock = path.join(root, '.dt-revision-edit.lock');
  try {
    fs.mkdirSync(lock);
  } catch (error) {
    if (error.code === 'EEXIST') throw Error('REVISION_EDIT_BUSY: another edit is active or awaiting recovery in ' + root);
    throw error;
  }
  try {
    const entry = latestEntry(root);
    const current = readCompletedVersion(root, entry);
    if (WORK_KEYS.some(key => current.layout[key] !== layout[key])) {
      throw Error('REVISION_FAMILY_CONFLICT: current directory ' + current.dir + ' belongs to another work');
    }
    if (entry.name !== requested) {
      throw Error('STALE_BASE_VERSION: ' + requested + ' is no longer current. Current version: ' +
        entry.name + ' (' + path.join(current.dir, 'layout.json') + ')');
    }
    if (canonical(layout) !== canonical(current.layout) || canonical(baseline) !== canonical(current.manifest)) {
      throw Error('BASE_VERSION_MISMATCH: use the persisted files in ' + current.dir);
    }

    const next = 'v' + (Number(entry.name.slice(1)) + 1);
    if (!VERSION.test(next) || !samePath(path.resolve(out), path.join(root, next))) {
      throw Error('NEXT_REVISION_REQUIRED: use a fresh output at ' + path.join(root, next));
    }
    if (newLayoutPath) {
      const target = path.resolve(newLayoutPath);
      if (!samePath(target, path.resolve(out, 'layout.json')) &&
          (!samePath(path.dirname(target), root) || fs.existsSync(target))) {
        throw Error('REVISION_LAYOUT_OUTPUT_EXISTS_OR_OUTSIDE_WORK: use a new work-root file or the new version layout.json');
      }
    }
    return await edit({baseDir, current: entry.name, next});
  } finally {
    fs.rmdirSync(lock);
  }
}

module.exports = {withCurrentRevision};

