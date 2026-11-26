# Local PSD renderer

The workbench uses `engine.cjs` for its startup check and for PNG/PSD export. The
engine loads the exact direct dependency versions declared in `package.json`.
It searches the engine's own `node_modules` first, then each existing local
`node_modules` path in `DT_PSD_DEPENDENCIES`, in order. Separate paths with
a semicolon on Windows or a colon on POSIX. A resolved package with a different
version or a failed native module load stops startup before an export is opened.
The variable contains dependency directories only.

For the synthetic smoke test, use an existing Node executable and run
`node tools/psd-engine/test-synthetic.cjs`. It checks PNG creation, PSD decoding,
native editable text, and exact embedded and recomposited pixel readback. It
uses a temporary folder and Arial, and performs no network or installation.
The old frozen H01 prototype's private image/layout baseline remains with its
source instance. Workbench startup and ordinary PSD export never read it.
