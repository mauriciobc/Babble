# Babble video playback plan: GstPlay + gtk4paintablesink

2026-11-09, revision 2 (after adversarial review). Tech Lead. Supersedes `reports/Babble updated streaming plan.md` and `reports/Babble streaming video implementation.md`. Evidence: `reports/Babble video playback options benchmark.md` and `reports/video-playback-bench/`.

## Goal

1. Stop the media viewer crashing.
2. Stream video, GIFV and audio inside the Flatpak.
3. Leave the media viewer smaller and clearer.

Inline timeline playback comes after that, separately gated.

## What is settled and what is not

Settled (measured 2026-11-09; every run is in the benchmark files):

| Claim | Evidence |
|---|---|
| `Gtk.MediaFile` cannot stream in the sandbox | `org.gnome.Platform//51`: `Gio.File.new_for_uri(https)` is `GDummyFile`, `read()` → "Operation not supported"; `GtkMediaFile` → "Failed to pause". GTK 4.24's builtin backend reads through GIO (`giostreamsrc`) (`probe.py`, `sandbox_probe.py`) |
| **The shipping viewer crashes** (E3b) | The real `MediaViewer` at `fd6f7b5`: 4 SIGSEGVs in ~415 open/close cycles (4 of 10 fifty-cycle runs), GstPlay use-after-free (coredumpctl 45798 signature). A reduced harness of the same teardown order did not crash in 504 cycles (E3), so the order alone is not the trigger |
| GstPlay + `gtk4paintablesink` streams in the sandbox with nothing bundled | Live post `mastodon.me.uk/@minesNarf/117581333386761171` played in the GNOME 51 runtime, which ships `libgstplay-1.0`, `GstPlay-1.0.typelib`, `GstPbutils-1.0.typelib`, `libgstgtk4.so`. **Correction (E6):** the base runtime's `libavcodec` decodes neither H.264 nor HEVC. Software H.264 comes from the `codecs-extra` extension; without it, only hardware decoders (VA, NVIDIA) play Mastodon video |
| A Python `Gtk.MediaStream` subclass can drive GstPlay under `Gtk.Video` | `stream_spike.py`: `do_play` from autoplay, forwarded snapshots, `prepared`/`playing`, seek, intrinsic size, no criticals |
| Signal closures that capture the wrapper leak it | RSS 554→2226 MB over 12 cycles until handlers were tracked and disconnected (`stream_cycles.py`) |
| A long-lived GstPlay did not crash | `reuse_cycles.py`: 72 host cycles + 6 sandbox cycles, 0 crashes, RSS flat |

**Settled by E1 (2026-11-09): per-viewer engine with deferred release.** Releasing a GstPlay is safe when the code calls `stop()`, waits for the adapter's `state-changed` == `STOPPED`, disconnects its handlers and drops the last reference from `GLib.idle_add`. Freeing it right after `stop()` is what crashes. Results below (§ E1 results).

## Adversarial review of revision 1

Revision 1 chose an app-lifetime engine that is "never finalised". Its problems:

### Major

1. **The crash cause was assumed.** The cheaper alternative was never tried: wait for STOPPED, then drop the reference from an idle callback. If that is safe, per-viewer engines work, and the singleton, leases, pre-emption, stuck-engine and exit problems all disappear. Closest public reports: [GNOME 793383](https://bugzilla.gnome.org/show_bug.cgi?id=793383), [gstreamer#2381](https://gitlab.freedesktop.org/gstreamer/gstreamer/-/issues/2381), [servo/media#456](https://gh.r-mj.com/servo/media/pull/456).
2. **"Never finalised" is false at exit.** `main()` returns from `app.run()` (`src/main.py:402-404`) and interpreter teardown frees module globals, which finalises the engine: the crashing operation, untested, on every quit.
3. **No recovery.** A singleton that cannot be recreated stays wedged after a GPU reset, a lost GL context or a display change.
4. **Wrong order.** The shipping crash waited behind a refactor and a network change. It can be fixed alone: same downloads, new engine, `file://` URI.
5. **The file-size goal was false.** Export (`mediaviewer.py:732-792`, `833-950`) is ~180 lines; `mediaviewer.py` (1058) would land near 880, still over 800. Export also uses `self.progress`, toasts, `self.current`, `self.picture` and `self.window`, so it is a mixin seam, not a free module.
6. **Thin statistics** (above).

### Moderate

7. **Metered contradiction.** It said metered turning on mid-play releases the stream, which stops playback the user asked for with Load. Metered gates starting only.
8. **Scope creep.** Remote-media consent (a privacy/product change that also touches image loading, `mediabox.py:621`), volume memory and the spinner do not fix the crash or enable streaming.
9. **"Prepared" was tied to `duration-changed`.** A stream with unknown duration would never be prepared. Prepared must come from preroll.
10. **The GL path is untested in CI.** The suite pins `GSK_RENDERER=cairo`, so the sink has no GL context and `glsinkbin` never runs there.
11. **Idle cost of a resident engine was not measured.**
12. **Test spec bug.** "Count `GObject.signal_handlers`" is not an API.

### Minor

13. The seek fallback timings were arbitrary.
14. Showtime is one GstPlay per window, not proof of "never finalise".
15. The Fedora package names were not confirmed.

## E1 results (2026-11-09)

Scripts and raw lines: `reports/video-playback-bench/e1_release.py`, `e1b_wrapper.py`, `e1c_reproducer.py`, `e1c_bundle.py`, `e1_run.sh`, `e1c_run.sh`; results in `e1_results.txt`, `e1c_results.txt`. All runs: headless mutter 1280×1024, crashes counted with `coredumpctl`. Every GstPlay was confirmed finalised through a GObject weak ref, so no clean result comes from a leak.

**1. The first harness did not reproduce the crash, so on its own it decided nothing.** `e1_release.py`: `Gtk.Picture` on the sink paintable; teardown is a plain `stop()` with no pause and no `Gtk.MediaStream`.

| Mode | Cycles | Crashes | Finalised |
|---|---|---|---|
| immediate (control) | 5×100 | 0 | 500/500 |
| deferred | 5×100 | 0 | 500/500 |
| deferred, sandbox, live post | 100 | 0 | 100/100 (first frame 100/100) |

`e1b_wrapper.py` (wrapper in `Gtk.Video`, pause then stop): 12 cycles per mode, 0 crashes in either. It also did not reproduce.

**2. The reproducer, one change per mode.** `e1c_reproducer.py` is `stream_cycles.py` + `streamwrap.PlayStream`: a `Gtk.Video`, teardown `pause()` → `stop()`, release, then remove the widget.

| Mode | The one change | 12-cycle runs | Runs that crashed |
|---|---|---|---|
| orig | none (release right after `stop()`) | 3 here + 4 before | **6 of 7** (SIGSEGV, same signature as coredumpctl 45798) |
| dropsink | also drop sink and paintable at once | 3 | **3 of 3** (so the kept sink reference is not the cause) |
| **deferred** | wait for `STOPPED`, release in `GLib.idle_add` | 3 + 25 | **0 of 28** (336 cycles, 336/336 finalised, RSS 518–538 MB, STOPPED 7–15 ms after `stop()`) |
| deferred, sandbox, loopback HTTP | same, inside `org.gnome.Platform//51` | 100 cycles | **0** (100/100 first frames, 100/100 finalised, STOPPED 7–14 ms) |
| deferred, sandbox, live post | same, against mastodon.me.uk | 100 cycles | **0** (100/100 finalised; only 2/100 reached a first frame inside the 1.5 s window, so this run mostly covers teardown during load, STOPPED 2–146 ms) |

**Reading.** At the control's rate (6 of 7 runs, roughly 1 crash per 7–10 cycles), 436 deferred cycles with no crash is not luck. The precise trigger is not identified: freeing right after a plain `stop()` was safe in 500 cycles, while freeing right after `pause()` → `stop()` under a `Gtk.MediaStream` in `Gtk.Video` crashed almost every run. Waiting for `STOPPED` removes it in both. Report upstream with `e1c_reproducer.py` as the reproducer.

**Decision.** Per-viewer engine with deferred release. No singleton, no leases, no pre-emption. E2 as written (resident engine) is not needed.

**New requirement (from E1, replaces E2): quit while playing.** Deferred release needs the main loop to turn until `STOPPED`, but quitting ends the loop. `do_shutdown` (`src/main.py:217`) must stop any live engine and iterate the main context until `STOPPED` (bounded; observed 2–146 ms) before `Adw.Application.do_shutdown`. Check: quit with a video playing, 50×, 0 crashes.

### Quit-while-playing results (2026-11-09)

`quit_check.py`, run by `e2e3_run.sh`, raw lines in `quit_results.txt`. A `Gtk.Application` plays the 30 s clip through the Phase 1a wrapper in `Gtk.Video` and quits 1 s after the first frame via `app.quit()`; the process ends with `sys.exit(app.run())`, as Babble's launcher does. Naive and mitigated runs alternated.

| Shutdown | Runs | SIGSEGV | STOPPED seen | Wait in `do_shutdown` |
|---|---|---|---|---|
| naive: nothing special, interpreter teardown frees the engine | 50 | **17 (34%)** | — | — |
| mitigated: `pause()`, `stop()`, turn the main context until `STOPPED` (1 s bound), disconnect, drop, then chain up | 50 | **0** | 50/50 | 11–20 ms |

The requirement holds: without it, about one quit in three crashes while a video plays. With it, 0 of 50, and the bound was never reached.

### E3 results: baseline for the shipping path (2026-11-09)

`e3_mediafile.py`, raw lines in `e3_results.txt`. `Gtk.MediaFile` on a local file in `Gtk.Video` (autoplay), torn down in `release_media` order (pause, `set_child(None)`, drop), with its one handler disconnected first. 1.5 s per cycle.

| Runs × cycles | SIGSEGV | First frames |
|---|---|---|
| 42 × 12 = 504 | **0** | 504/504 |

**This corrects an earlier claim.** The benchmark's `Gtk.MediaFile` crashes (2 of 4 twelve-cycle runs) came from the benchmark harness (`bench.py`). It left three handlers connected, with closures capturing the candidate object, and released 1 s after the first frame. In Babble's teardown pattern as reproduced here, the shipping path did not crash. Whether the real `MediaViewer` crashes is **not established**. It uses `pages.connect_weak` handlers on the stream (`src/mediaplayback.py:351-356`), which this harness does not reproduce. Next check (E3b): drive the real `present_media` / `close()` 500 times, extending `src/test-mediaviewer-teardown.py`.

Consequence for the plan: Phase 1a is still needed, because `Gtk.MediaFile` cannot stream in the sandbox. Its urgency as a crash fix depends on E3b.

### E3b results: the real viewer (2026-11-09)

`e3b_viewer.py`, run by `e3b_run.sh`, raw lines in `e3b_results.txt`. Babble's own code at `fd6f7b5`, unmodified:
- `present_media()` on an `Adw.ApplicationWindow` overlay;
- a `video` attachment on odd cycles, `gifv` on even ones, served over loopback HTTP;
- the real `_show_stream` → `_download_stream` (libsoup) → `_start_stream` (`Gtk.MediaFile`, `pages.connect_weak` handlers, idle inhibition) → autoplay;
- 1.5 s of playback, then the real `viewer.close()` (`_unload_media` → `release_media`) and `gc.collect()`.

Metered/power-saver are forced off. `G_DEBUG=fatal-criticals`, headless mutter, default renderer.

| Run (50 cycles each) | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| Result | **SIGSEGV** after cycle 37 | 50/50 | 50/50 | **SIGSEGV** after 21 | **SIGSEGV** after 7 | 50/50 | 50/50 | **SIGSEGV** after 46 | 50/50 | 50/50 |

- **4 crashes in ~415 cycles (≈1%), and 4 of 10 runs.** Every cycle that got that far played (411/411); 0 GTK criticals.
- Core dumps, matched by PID (an earlier per-run count picked up a dump from the previous run):
  - PIDs 273141, 274753 and 276409: `g_type_check_instance_is_fundamentally_a` ← `g_object_ref` ← `gst_object_ref` ← `gst_message_new_custom` ← `libgstplay` ← `gst_bus_async_signal_func`, the signature of coredumpctl 45798;
  - PID 275099: `g_type_check_instance_is_fundamentally_a` ← `g_object_unref` ← `libgstplay` thread exit.
  
  Both are GstPlay using a freed object.
- The crash cannot be tied to video vs GIFV: a crash after the line for cycle N happened during N's delayed teardown or during N+1.

**Conclusion: the shipping viewer crashes**, about once per hundred open/close cycles of a playing video or GIFV, in its real code path. `release_media` order does not prevent it (E3 shows the order alone is not the trigger; the full viewer path is). Phase 1a is an urgent crash fix, not only the first step towards streaming.

### E4 results: drawing through the Python wrapper (2026-11-09)

`e4_forward.py`, run by `e4e5_run.sh`, raw lines in `e4_results.txt`. The same GstPlay + `gtk4paintablesink` (in `glsinkbin`, GL renderer) drawn two ways:
- **wrapper:** `Gtk.Video` on the Python `Gtk.MediaStream` wrapper (the Phase 1a design);
- **direct:** `Gtk.Picture` on the sink paintable.

10 s of playback per run, starting 1 s after the first frame. Main-thread CPU comes from `/proc/self/task/<main>/stat`; Python time is wall time inside the wrapper's vfuncs and relays. Stalls are a 4 ms timer firing more than 16 ms late.

| Clip | Mode | Main-thread ms per frame (5 runs) | Python µs per frame | Rendered / dropped | Stalls >16 ms (max ms) |
|---|---|---|---|---|---|
| 720p30 | wrapper | 1.860, 1.833, 1.728, 1.827, 1.867 | 114.4, 113.3, 108.4, 114.9, 115.6 | 300–301 / 0 | 0 (1.5–2.0) |
| 720p30 | direct | 1.661, 1.661, 1.595, 1.633, 1.628 | — | 300–301 / 0 | 0 (1.6–1.8) |
| 1080p60 | wrapper | 0.849, 1.033, 1.115, 1.048, 1.167 | 60.5, 73.7, 77.7, 72.9, 76.7 | 600–601 / 0 | 0 (1.0–1.2) |
| 1080p60 | direct | 0.850, 0.715, 0.649, 0.932, 0.900 | — | 600–601 / 0 | 0 (0.8–1.0) |

- **Python adds ~0.06–0.12 ms per video frame**, under a tenth of the 1 ms stop condition, with no stalls and no drops. **E4 passes**: keep `Gtk.Video` with the wrapper; the `Gtk.Picture` fallback is not needed.
- "Per frame" divides by video frames. The main thread's own work runs per 60 Hz frame-clock tick, which is why the 30 fps figures are higher per frame.
- The wrapper must preroll (`pause()` after `set_uri()`). Without it the stream never becomes prepared, so `Gtk.Video` never autoplays; the first E4 smoke run hit exactly that. It confirms "prepared from preroll" in § Architecture.

### E5 results: memory (2026-11-09)

`e5_idle.py`, raw lines in `e5_results.txt`. RSS (MB) of the whole process after `gc` and a 2 s settle; 5 s GIFV clip, NVIDIA decode (`nvh264dec`), GL renderer.

| Stage | Engine (Phase 1a design), 3 runs | `Gtk.MediaFile` (today) |
|---|---|---|
| baseline (window, `Gst.init`) | 299.7, 300.0, 299.9 | 300.1 |
| engine created, never played | 374.0, 374.3, 374.2 | — |
| playing | 559.0, 559.2, 558.9 | 559.5 |
| released | 422.4, 422.6, 422.2 | 422.8 |
| after 24 more cycles | 560.1, 560.4, 554.5 | 476.5 |

- **Today's path crashed in 3 of its 4 E5 runs** during the 24 cycles (core dumps 280427, 280961, 281382, all `gst_message_new_custom`). Its column is the one run that finished. More evidence for E3b; it also shows E3's 0/504 depended on its timing.
- **Playing and released cost the same in both designs** (559 and 422 MB).
- **Creating an engine costs ~74 MB before anything plays.** That's the sink's GL context and the GstPlay thread. Create it on the first video or GIFV a viewer shows, not when the viewer opens on an image.
- **After repeated cycles the engine path settles ~80 MB higher** than the one surviving `Gtk.MediaFile` run (555–560 against 476). E1's 100-cycle runs ended at 556–569 MB, so this is a plateau, not a leak. Cause not yet attributed: per-sink GL contexts and the CUDA decoder context are candidates, both driver-dependent. Re-measure on Intel/Mesa and with software decode in E6. Not a stop condition. `PERFORMANCE.md` has no media memory budget (its 96 MB is the timeline benchmark's measured retained RSS, a different workload). Phase 1a should add a media line to `PERFORMANCE.md` with these figures.

### E6 results: platform checks (2026-11-09)

Scripts: `e6_probe.py`, `e6_server.py`, `e6_decode.sh`, `e6_play.sh`, `e6_mem.sh`. Raw results: `e6_libs.txt`, `e6_decode.txt`, `e6_play.txt`, `e6_mem.txt`. Sandbox = `org.gnome.Platform//51` (GStreamer 1.28.6) with `--device=dri`. Machine: Intel UHD (`renderD128`, 0x8086) and NVIDIA RTX 3060 (`renderD129`, 0x10de).

**1. Audio in the Flatpak: silent today.** `audiotestsrc ! autoaudiosink` in the sandbox:

| `finish-args` | Sink chosen | Result |
|---|---|---|
| no `--socket=pulseaudio` (Babble today) | `fakesink` | EOS, **no error**: silence the user cannot diagnose |
| `--socket=pulseaudio` | `pulsesink` | EOS, sound |

→ Phase 1a adds `--socket=pulseaudio` to the local dev manifest (`build-aux/flatpak/io.github.mauriciobc.Babble.json`; a Flathub manifest is human-written). No test can catch the silent fallback through errors; the Flatpak CI step should check `finish-args`.

**2. Codecs: H.264 depends on `codecs-extra`.** Querying `avcodec_find_decoder_by_name` in each library:

| `libavcodec` | h264 | hevc | vp9 | av1 |
|---|---|---|---|---|
| base runtime (`/usr/lib/x86_64-linux-gnu/libavcodec.so.62`) | **no** | **no** | yes | yes |
| `codecs-extra` (`…/codecs-extra/lib/libavcodec.so.62`) | yes | yes | yes | yes |

The runtime declares `codecs-extra` (`26.08-extra`) without `no-autodownload`, so Flatpak installs it with the runtime by default. Verified from the metadata, not by a clean install. Without it, Mastodon video (H.264) plays only through a hardware decoder. On a machine with none (a VM, an old GPU, a driver without VA), every video fails. → The codec error is a main path, not an edge case. A missing-plugin message for H.264 or HEVC must name `codecs-extra`.

**3. Decode paths in the sandbox, 1080p60, decode only, 3 runs each, all 600 frames:**

| Decoder | H.264 CPU ms per frame | HEVC CPU ms per frame |
|---|---|---|
| auto (`decodebin3` picked `nvh264dec` / `nvh265dec`) | 0.29, 0.30, 0.26 | 0.24, 0.25, 0.28 |
| software (`avdec_*`, from `codecs-extra`) | 5.85, 6.10, 6.25 | 9.28, 9.63, 9.38 |
| Intel VA (`va*dec`) | 1.44, 1.48, 1.45 | 1.42, 1.43, 1.51 |
| NVIDIA (`nv*dec`) | 0.26, 0.26, 0.26 | 0.22, 0.23, 0.25 |

Every path works. Software HEVC at 1080p60 needs ~56% of one core; Mastodon transcodes uploads to H.264, so HEVC reaches Babble only from other software's `remote_url`.

**4. Sources in the sandbox** (GstPlay + `gtk4paintablesink`, first frame, then seek 1 s later; position read 3 s after the seek):

| Source | First frame ms (2 runs) | Seek | Result |
|---|---|---|---|
| `files.mastodon.social` (Varnish CDN, `206`, faststart; an 87.7 MB, 43 s, 1080×1920 H.264 video from public trends) | 2107, 731 | to 20 s → 21.96, 22.08 | works. Download-first needs all 87.7 MB before a frame |
| 302 redirect (loopback) | 611, 745 | to 5 s → 7.89, 7.90 | works |
| no Range support (always 200) | 762, 762 | **fails**: `souphttpsrc` "Could not seek in resource", delivered as a playback **error** | plays, seek kills it |
| plain loopback | 811, 635 | to 5 s → 7.90, 7.90 | works |

→ New requirement for `EngineStream`: pass `seekable` from `GstPlay.PlayMediaInfo.is_seekable()` to `stream_prepared`, so `Gtk.Video` disables seeking on such sources. A seek on a non-seekable source must never reach the engine. Add the no-Range case to `test-media-progressive.py`.

**5. The E5 plateau is the NVIDIA decoder.** Engine path, decoder forced by rank (`GST_PLUGIN_FEATURE_RANK`) and confirmed in use; RSS in MB, 2 runs:

| Decoder | Created | Playing | Released | After 24 cycles |
|---|---|---|---|---|
| `nvh264dec` | 374.2, 374.2 | 559.1, 558.9 | 422.5, 422.3 | 560.5, 554.6 |
| `vah264dec` | 374.3, 374.1 | 424.4, 424.0 | 424.5, 424.2 | 439.1, 435.9 |
| `avdec_h264` | 374.1, 374.3 | 432.1, 428.0 | 431.6, 427.4 | 457.6, 453.2 |

The NVIDIA (CUDA) decoder costs ~135 MB more while playing, and that memory is not returned. With VA or software decoding the engine path settles at 436–458 MB. Engine creation is +74 MB with every decoder: create it lazily (§ E5). Not a design problem; recorded for `PERFORMANCE.md`.

**6. Fedora rawhide names, confirmed** (mdapi, fc46): `gstreamer1`, `gstreamer1-plugins-base`, `gstreamer1-plugins-good`, `gstreamer1-plugins-bad-free` (all 1.28.9), `gstreamer1-plugins-bad-free-libs` (ships `libgstplay-1.0.so.0` and `GstPlay-1.0.typelib`), `gstreamer1-plugin-gtk4` 0.15.2 (ships `libgstgtk4.so`). The § CI list stands as written.

## Phase 0: decide the lifetime model (experiments, no app code)

All in `reports/video-playback-bench/`, headless mutter 1280×1024, `G_DEBUG=fatal-criticals`, crashes counted with `coredumpctl list --since`.

| # | Experiment | Stop condition / decision |
|---|---|---|
| **E1 ✅ done** (per-viewer chosen; see E1 results) | **Deferred release.** Per-media GstPlay: `stop()`, wait for adapter `state-changed` == `STOPPED`, disconnect tracked handlers, drop the last reference in `GLib.idle_add`. 500 local cycles + 100 against the live post in the sandbox | 0 crashes → **per-viewer engine** (below). Any crash → **resident engine**, and E2 becomes required |
| E2 (not needed; replaced by the quit-while-playing check in E1 results) | **Quit safety** (resident only). Create the engine, play, quit via `Gtk.Application.quit()` and normal interpreter exit, 50×. Mitigations to try: in `do_shutdown` (`src/main.py:217`) `stop()` and wait for STOPPED; then keep a C-owned reference, or `os._exit` after shutdown | Any crash with every mitigation → stop and escalate |
| E3 ✅ done | **Baseline soak.** 500 `Gtk.MediaFile` cycles | 0 crashes in 504 cycles in Babble's pattern (see E3 results) |
| E3b ✅ done | **Real viewer soak.** `present_media` / `close()` 500×, extending `test-mediaviewer-teardown.py` | 4 crashes in ~415 cycles → Phase 1a is an urgent crash fix |
| E4 ✅ passed | **Forwarding cost.** Python `do_snapshot` + `invalidate_contents` relay vs `Gtk.Picture(sink paintable)`; main-thread ms/frame at 720p30 and 1080p60, GL renderer | >1 ms/frame or any new `stalls_over_16ms` → draw with `Gtk.Picture` and overlay `Gtk.MediaControls(stream)` |
| E5 ✅ measured | **Idle cost.** RSS with no engine, engine never used, after one play + `stop()`, after 24 cycles | Feeds the per-viewer vs resident trade-off |
| E6 ✅ done (see E6 results) | **Platform checks.** Audio in the Flatpak (no `--socket=pulseaudio` today); Intel VA (`vah264dec`) and software (`avdec_h264`) decode; `avdec_h265` ± `codecs-extra`; a CDN/S3 media host with redirects; `dnf repoquery` for the Fedora package names | Silent audio → add the socket to the local dev manifest (a Flathub manifest is human-written) |

## Architecture

The modules are the same under either outcome of E1; only the lifetime rule differs.

```
mediaengine.py   Engine: GstPlay + gtk4paintablesink (glsinkbin when the paintable
                 has a GL context; Showtime's play.py shape). Handlers connected once,
                 ids tracked. Events relayed to the owner by method call, never closure.
                 GstPbutils missing-plugin messages; BUFFERING surfaced.
mediastream.py   EngineStream(Gtk.MediaStream): forwards the paintable, maps engine
                 events to stream_prepared/update/seek_success/stream_ended/gerror;
                 release() makes every vfunc a no-op.
mediaplayback.py MediaPlayback mixin: the viewer's play/defer/error/inhibit policy.
mediaexport.py   MediaExport mixin (Phase 1c).
mediaviewerchrome.py  chrome construction (Phase 1c).
```

Lifetime rule:

- **If E1 passes (per-viewer).** The viewer creates an engine when it first shows a video, GIFV or audio, and releases it on close: `stop()` → STOPPED → disconnect → drop in idle. Navigation between attachments reuses it with `stop()` + `set_uri()`. No singleton.
- **If E1 fails (resident).** One lazy `engine()` created on the main thread after the display exists, with one lease holder at a time. Release = `stop()`. Shutdown follows whatever passed E2. A pipeline error triggers `stop()` and `set_uri()` again; if errors repeat, report and offer Open in Browser.

Stream details (both outcomes):

- **Prepared from preroll.** `stream_prepared(has_audio, has_video, seekable, duration)` on the first `state-changed` to `PAUSED` (or `media-info-updated`), with the media info's audio/video and seekability. Duration is optional (0 when unknown) and updated when `duration-changed` arrives.
- **Seek completion.** `seek_success()` on `seek-done`. Failing that, on the first `position-updated` after the seek, or after a timeout. Both paths are tested.
- **Teardown contract (replaces `release_media`):** `stream.pause()`, `stream.release()`, then drop the widget. The engine's lifetime rule, not widget order, is what keeps GstPlay safe.

## Phases

### Phase 1a: fix the shipping crash (smallest change, ships alone)

- Add `mediaengine.py` and `mediastream.py` with the lifetime rule from E1/E2.
- `mediaplayback.py`: `_start_stream` (`src/mediaplayback.py:319`) builds an `EngineStream` on the **downloaded file's `file://` URI**. Downloads, metered deferral, the GIFV gate, idle inhibition, `Gtk.Video`, `Gtk.MediaControls` (audio, `src/mediaplayback.py:391`) and the GIFV `Gtk.Picture` stay.
- Delete the `Gtk.MediaFile` path and `release_media`.
- Tests: `test-media-engine.py`, `test-media-stream.py`; `test-mediaviewer-teardown.py` rewritten to 24 rapid open/close cycles + the 5 invariants; `test-timeline-memory.py` viewer section kept green.
- CI and `CLAUDE.md` changes below.

### Phase 1b: stream instead of download

- `EngineStream` on the remote URL; no temp copy.
- Save As / Open With / drag-out fetch on demand with `download()` into `_local_files`; drag offers the URL until a copy exists.
- Metered gates **starting** (preview + Load, as today). Playback the user started continues if metered turns on. Leaving the viewer always releases, which stops the read-ahead.
- Buffering spinner after 1 s of `BUFFERING`.
- Codec error from `GstPbutils` missing-plugin messages: names `codecs-extra` when a decoder it carries is missing; translatable; no element names; never `ffmpeg-full`.
- `test-media-progressive.py`.

### Phase 1c: viewer refactor (behaviour-neutral)

- `MediaExport` mixin in `mediaexport.py`: copy URL/image, Save As, Open With, drag providers, local-file cache. It follows the `MediaPlayback` mixin precedent (`src/mediaplayback.py:210`, "needs it as `self`").
- Chrome construction (`_build`, `_build_bottom`, `mediaviewer.py:277-580`, ~300 lines) moves to `mediaviewerchrome.py`, a builder returning the widgets the viewer keeps.
- Target: `mediaviewer.py` ≈ 600 lines. Remove it from `EXCEPTIONS` in `src/test-file-size.py`. New modules go into `src/meson.build` and `po/POTFILES.in`.

### Phase 1d: small items, one PR each

- `may_autoplay_loop()`: `gtk-enable-animations`, `gtk-interface-reduced-motion`, power-saver, live via `sysstate` `notify::` (replaces the one-time check at `src/mediaplayback.py:342`).
- Volume memory (new gschema key).
- `graphics-offload` on the viewer's `Gtk.Video`.

**Out of this plan:** remote-media consent (Babble falls back to `remote_url` silently today: `mediaplayback.py:272`, `mediabox.py:621`, `mediaviewer.py:606,765`). It is a privacy behaviour change that also touches images, so it gets its own proposal and decision.

### Phase 2: inline timeline playback (gated)

Preconditions:
- Phase 1 stable.
- `statusrow.py` extraction PR (793/800).
- Performance limits in `PERFORMANCE.md` / `autoresearch.sh` (`stall_max_ms`, `stalls_over_16ms`, `main_ms`, RSS) with a stop condition.
- Settings: autoplay and data-saver keys, Preferences, `ui strings`, de/es/fr/pt_BR, help.

Design depends on E1:
- **Per-viewer passed:** one engine per active tile, at most one active, released on scroll-away with the deferred release.
- **Resident:** a second app-lifetime engine owned by `mediainline.py`, lent to the visible tile.

Either way: muted loop only, ≥50% visible, poster from `preview_url`, duration badge from `meta`, no idle inhibition, keyboard-reachable pause control, `may_autoplay_loop()` + metered + the preference.

#### Phase 2a design (2026-11-10)

Preconditions met: room in `statusrow.py` (#98, 678 lines), the gate (#99, `bench/compare.py` + `bench/limits-inline.json`), the settings (#100: `autoplay-animated-media`, `media-autoload`). E1 passed per-viewer, so: **one engine at a time, created on demand, reused between tiles, released by the deferred release.**

- **Scope: GIFV only.** Mastodon's `gifv` is a muted loop by definition. Videos keep their poster and play button; they open the viewer as today.
- **`src/mediainline.py`, `InlinePlayer`:** one per window (it is window-owned state), owning at most one `mediaengine.Engine` and one `EngineStream`.
  - **Registration:** a GIFV tile in `MediaBox` registers with the window's player when mapped and unregisters on unmap or rebind (rows recycle). The player holds tiles weakly, never by a closure that captures them.
  - **Candidates:** on scroll (the ancestor `Gtk.ScrolledWindow`'s vadjustment, coalesced to one idle evaluation), map, unmap and window resize, the player picks the registered tile with the largest visible fraction, provided it is ≥ 50% inside its scrolled window's viewport.
  - **Gates:** `may_autoplay_loop(tile)` and not `should_defer_stream(media-autoload, metered)`, re-evaluated live on the same signals the viewer's loop gate uses, plus `changed::media-autoload` and `notify::metered`.
  - **Switching:** with no engine yet, it creates one (lazily, +74 MB). It loads the attachment's URL (`url`; the remote-media consent is never bypassed: a tile whose URL is remote under `remote-media` = ask/never is not a candidate), sets loop and mute, and puts the stream on the tile's `Gtk.Picture`. Switching tiles is `stop()` + `load()` on the same engine, as viewer navigation does. The previous tile gets its poster texture back.
- **Release:**
  - No candidate for 2 s, the window hidden or closed, or the media viewer opening (it has its own engine) → stop, restore the poster, then `engine.release()` (deferred). The next candidate creates a fresh engine.
  - Never more than one live inline engine. Up to two while the viewer is open; the viewer case is outside the bench workload.
- **Tile UI:** the poster stays until the first frame (no black flash). The GIF badge stays. While playing, a small OSD **Pause Animation** button is reachable by Tab inside the tile and toggles pause. A user pause sticks to that attachment (`item.ui`) until the row is rebound with a new status. Clicking the tile still opens the viewer. No idle inhibition.
- **Not in 2a:** videos, sound, a duration badge (no reliable `meta` duration for GIFVs), a separate inline preference. If the gate fails twice, the stop condition's "off by default behind its preference" needs a separate `inline-animated-media` key, because `autoplay-animated-media` also drives the viewer. Add it only then.
- **Bench:** the fixture server serves a real looping clip (Ogg/Theora, the suite's fixture codec, so it runs on any machine) for `gifv` attachments. The workload is otherwise unchanged. A baseline from main against the candidate with inline on, `--limits bench/limits-inline.json`, is the acceptance gate (stop condition in `PERFORMANCE.md`).
- **Tests:**
  - `media inline`: one candidate at a time; ≥ 50% rule; switching reuses the engine; scroll-away → poster restored and the deferred release; each gate closes it, live; pause control by keyboard; remote-under-ask is never played; window close leaves `mediaengine._all` empty.
  - Leak check in `timeline memory` style: rows and tiles freed with a player alive.

### Phase 3 / 4

- **Phase 3:** notifications and Catch Up open the viewer; PeerTube cards go to the browser; remote-url recovery when transcoding is pending.
- **Phase 4:** tracks and HLS via GstPlay's `playbin3` when the data model needs them.

### Addendum (after Phase 3): cheaper inline playback, from the maintainer's autoresearch run (2026-11-10)

Inline GIFVs shipped off by default (#101) because the opt-in configuration failed the gate:
- a one-time ~150 ms stall on the first engine;
- +85 MB that stays loaded after first use;
- main-thread time +26%;
- an unexplained ~1.3 s stall in 1 of 5 runs.

The maintainer's autoresearch session proposed fixes on `fix/inline-long-stall` (commits `9ed45fd`…`c813489`). Evaluate them as a proper milestone, with the Dev/QA/Tech Lead model, in its own worktree, once nothing else is benchmarking.

**Candidate changes**
1. **Defer the sink's paintable** (`mediaengine.py`, `_get_paintable()` instead of `sink.props.paintable` in `__init__`). For inline frames (`gl=False`) no GL context would ever be created. That may remove most of the first-play stall and of the +85 MB, which are NVIDIA GL libraries. The most valuable idea.
2. **Remove `pause()` from `load()`.** Reported to save ~120 ms. Questioned:
   - `GstPlay.set_uri()` does not start the pipeline.
   - GstPlay's `pause()` is asynchronous.
   - `load()` = `set_uri` + `pause` is the documented contract the viewer relies on: preparing from preroll, a GIFV prerolling to a still frame under reduced motion, and the release timing behind the crash fix.
3. **Show the stream on `prepared`** instead of on the first painted frame. It avoids touching the paintable early, but risks the blank flash the poster rule prevents.
4. **Relax `retained_mb`** in `bench/limits-inline.json` from +5 MB to +90 MB. Rejected as proposed: it removes the leak check. Replace the mis-specified limit instead, with a tight bound around the measured cost once GL is avoided, or a cycles-versus-one leak check.

**The reported figures need re-measuring.** The branch head `4bddaba` ("DEBUG: skip engine creation") makes `InlinePlayer._start()` return at once, so nothing plays. Two results point to that state: "inline on" faster than the inline-off baseline (main_ms −8%), and RSS growth +10 MB against +85 MB whenever a GIF really played. Measure `c813489` (without the DEBUG commit) instead.

**Acceptance**
- **Viewer path unchanged where it matters:** `glsinkbin` is still chosen when the paintable has a GL context. No deadlock from creating the paintable lazily in a snapshot or a streaming thread.
- **Crash gates on the changed engine:** viewer crash gate 250 cycles with 0 crashes, quit gate 20/20, `media inline` loop 20/20.
- **`media inline`'s "poster replaced in one step, never blank" check still passes.**
- **Bench, same machine and session:**
  - the default (off) passes `bench/limits.json` against main;
  - inline on is compared with `bench/limits-inline.json` and reported in full;
  - per-run stall figures, so the 1.3 s outlier is visibly gone or still there.
- **If inline on passes:** propose turning `inline-animated-media` on by default as a separate decision for the maintainer.

**Outcome (PR #103, 2026-11-10).**
- Candidates 1–3 were measured and not adopted:
  - removing `pause()` stops anything playing (7 starts, 0 frames shown at `c813489`);
  - a lazy paintable moves the GL-context stall to preroll, with the same memory.
- Instead, `Engine(gl=False)` decodes into an appsink and shows `Gdk.MemoryTexture` frames, so inline playback creates no GL context.
- Inline on, 5 runs each against main:
  - `stall_max_ms`: 106–133 → 30–34;
  - `retained_mb`: ~160 → ~91;
  - the 1.3 s outlier: 1/15 → 0/15.
- Inline on still misses `limits-inline.json` on `stall_max_ms` (32.4 vs ~31) and `retained_mb` (+14 vs +5, the one-time GStreamer load). It stays opt-in.

### Queued after #103 (2026-11-10)
1. **Leak-based `retained_mb` (Tech Lead decision; the maintainer deferred it).**
   - Replace the absolute +5 MB with an entry-cost bound (inline on ≤ ~+20 MB over off), plus a cycles-versus-one leak check (< 1 MB per extra create/play/release cycle).
   - Add an `inline_frames_shown` metric, so a candidate that plays nothing cannot win a bench.
   - Fix `BENCH_INLINE=0` still turning inline on.
2. **Suite cleanup.**
   - Unregister `nareal plugin` (an external responsibility; move `test-nareal.py` beside the plugin, runnable by hand).
   - Make `streaming helpers` skip immediately.
   - Profile `compose widgets` (55 s).
3. **Short silent videos inline (optional, maintainer to confirm).**
   - Rule: type `video`, no audio fields in `meta`, ≤ ~15 s; same engine, gates, Pause button and key.
   - Must be measured with the inline bench first (e.g. 1080×1080 at 5 Mbit/s is far heavier than a typical GIFV).

### Animated custom emoji (maintainer request, 2026-11-10)
Custom emoji arrive with `url` (animated APNG or GIF, e.g. ursal.zone's `:akko_aaa:` is an 11-frame looping APNG) and `static_url` (first frame). Babble draws only `static_url` (`src/textemoji.py:112`, `src/emojichooser.py:143`), so no custom emoji moves. This is independent of GstPlay: emoji are tiny, numerous images, not media attachments.

**Design constraints**
- **Always on (maintainer decision, 2026-11-10): no opt-in key, not tied to `inline-animated-media`.** The system accessibility gates still apply live: no animation under reduced motion, animations off, or power saver (`may_autoplay_loop`), showing the static frame instead. Animate only while visible.
- **Decoder.** GdkPixbuf/glycin give the first frame only for APNG.
  - Options to measure: GStreamer (`apngdec` is not in gst-plugins-good; check availability), a small pure-Python APNG/GIF frame splitter that composes frames into `Gdk.MemoryTexture`s, or glycin's animation API if the GNOME 51 runtime exposes it.
  - Decode once per emoji URL and share frames across every occurrence: emoji repeat heavily.
- **One clock, not one timer per emoji.** A single frame-clock tick per window advances every visible animated emoji (one `Gdk.Paintable` per emoji URL, invalidated per frame), so 50 visible emoji cost one callback, not 50.
- **Memory bound.** Decoded frames are width × height × 4 × frames per emoji (90×100×4×11 ≈ 400 KB). An LRU cache with a byte budget, dropped when nothing visible uses it. Row recycling must not keep frames alive (`timeline memory` check).
- **Accessibility.** Animation never carries meaning; the accessible label stays the shortcode. A global pause follows the reduced-motion setting live.
- **Where.** Post bodies and display names first (`textemoji.py`); then reactions (`announcements.py`) and the emoji chooser.

**Phase 0 (measure before building)**
- **Decoder choice:** time and memory to decode a sample of real animated emoji from a few instances.
- **Cost of a timeline full of them:** a timeline bench fixture with many animated emoji, measuring main-thread time per frame and stalls with inline on vs off.

**Acceptance**
- Because it is always on, the **default** bench, with a fixture containing animated emoji, must pass `bench/limits.json` against main with no limit changed. There is no opt-in key to hide a cost behind.
- `timeline memory` shows rows and frames freed.
- A test that reduced motion stops every emoji live.

## Tests

All `src/test-*.py`, headless mutter 1280×1024, `GSK_RENDERER=cairo`, `LANG=C`, `GSETTINGS_BACKEND=memory` set before `gi`. A missing element is not an allowed skip.

| Test | Asserts |
|---|---|
| `test-media-engine.py` | Lifetime rule (per E1/E2); handler ids tracked and all disconnected on release; the engine object is finalised (per-viewer) or kept stopped (resident), checked with a GObject weak ref; 24 cycles with no crash and RSS within ±20 MB |
| `test-media-stream.py` | Under `Gtk.Video`: prepared from preroll with no duration; `playing`/`ended`/`error`; seek with `seek-done`, without it (position fallback), and timeout; `release()` makes vfuncs no-ops; GIFV in `Gtk.Picture`; audio in `Gtk.MediaControls` |
| `test-mediaviewer-teardown.py` (rewritten) | 24 rapid viewer open/close cycles; viewer disposed, stream released, engine stopped or finalised per rule, child removed, not playing |
| `test-media-progressive.py` (1b) | Loopback Range server + committed Ogg/Theora fixture: first frame before full download; reads stop after release; 200-non-range plays; missing plugin → translated codec error (fake element list) |
| `test-mediaviewer.py` (1b/1c) | Export fetches on demand; drag offers the URL first |
| `test-media-gates.py` (1d) | Metered blocks starting but not user-started playback; power-saver and reduced motion block GIFV autoplay; live `notify::` pauses; idle inhibition only for video/audio. Fakes `sysstate` and motion settings |
| `file size rule` (1c) | `mediaviewer.py` out of `EXCEPTIONS`; new modules registered |

**Known gap:** the suite runs on Cairo, so `glsinkbin` and the GL upload are never exercised in CI. Cover them with the E4 bench and a manual check on the GL renderer before each release that touches media.

## CI (`.github/workflows/ci.yml`), Phase 1a

Fedora `test` job, add to `dnf install` (names confirmed by E6 first):

```sh
gstreamer1 gstreamer1-plugins-base gstreamer1-plugins-good \
gstreamer1-plugins-bad-free gstreamer1-plugins-bad-free-libs \
gstreamer1-plugin-gtk4
```

New step after the install:

```yaml
      - name: Check the media stack
        run: |
          for e in playbin3 souphttpsrc oggdemux theoradec gtk4paintablesink glsinkbin; do
            gst-inspect-1.0 --exists "$e" || { echo "::error::missing GStreamer element $e"; exit 1; }
          done
          python3 -c "import gi; gi.require_version('GstPlay', '1.0'); gi.require_version('GstPbutils', '1.0'); from gi.repository import GstPlay, GstPbutils"
```

Flatpak job, after the build:

```yaml
      - name: Check the runtime's media stack
        run: |
          flatpak-builder --run builddir build-aux/flatpak/io.github.mauriciobc.Babble.json \
            sh -c 'for e in souphttpsrc playbin3 gtk4paintablesink glsinkbin; do gst-inspect-1.0 --exists "$e" || exit 1; done
                   python3 -c "import gi; gi.require_version(\"GstPlay\", \"1.0\"); from gi.repository import GstPlay"
                   gst-inspect-1.0 --exists avdec_h264 && echo "avdec_h264 present (codecs-extra)" || echo "::warning::no software H.264 decoder: codecs-extra not in this image"'
```

`avdec_h264` is reported, not asserted. It exists only when `codecs-extra` is installed (E6), and the CI image may not carry it.

## Docs

- `CLAUDE.md`, "Tearing a video down": replace the `release_media` rule with the engine lifetime rule chosen in Phase 0 and the teardown contract.
- `MEMORY-LEAKS.md`: engine handlers are connected once and tracked; owners get method calls, never closures.
- `PERFORMANCE.md`: E4 and E5 results; the Phase 2 limits.
- Upstream: file a GStreamer issue with `stream_cycles.py` as the reproducer, whatever E1 shows.

## Risks

| Risk | Mitigation |
|---|---|
| E1 and E2 both fail | Stop; keep download-then-play on `Gtk.MediaFile` with the rapid-cycle test marked as a known crash; escalate upstream |
| Python per-frame forwarding cost | E4 stop condition; `Gtk.Picture` + `Gtk.MediaControls` fallback |
| Resident engine wedged (resident outcome only) | `stop()` + `set_uri()` again; Open in Browser after repeated errors |
| Read-ahead on metered networks | Metered never starts; leaving releases |
| GL path untested in CI | E4 bench + manual GL check |
| Only NVIDIA decode measured | E6 |
| Silent audio in Flatpak | E6; local manifest socket |

## Status and next actions (2026-11-09)

| Item | Status |
|---|---|
| E1 deferred release | ✅ per-viewer engine chosen (0 crashes in 436 deferred cycles against 6 of 7 crashing control runs) |
| Quit while playing | ✅ mitigation required and works (naive 17/50 crashes, mitigated 0/50) |
| E2 resident quit safety | not needed (no resident engine) |
| E3 `Gtk.MediaFile` baseline | ✅ 0/504 in Babble's pattern; corrects the benchmark's "shipping crash" claim |
| E3b real viewer soak | ✅ **the shipping viewer crashes**: 4 SIGSEGVs in ~415 cycles (4 of 10 runs) |
| E4 forwarding cost | ✅ passes: +0.06–0.12 ms per frame from Python, 0 stalls, 0 drops; keep `Gtk.Video` + wrapper |
| E5 idle memory | ✅ measured: engine creation +74 MB (create lazily); released state equal to today; repeated-cycle plateau ~80 MB above today's path on NVIDIA, cause to attribute in E6. Today's path crashed in 3 of 4 E5 runs |
| E6 platform checks | ✅ audio silent without `--socket=pulseaudio`; H.264/HEVC software decode only via `codecs-extra`; all decode paths work in the sandbox; CDN and redirects stream and seek; no-Range sources error on seek (new `seekable` requirement); E5 plateau = NVIDIA decoder; Fedora names confirmed |

**Phase 0 is complete.**

| CI fix-up (2026-11-10, merged forward into #92–#96) | CI was red on all five PRs, though every local gate had passed. (1) Flatpak job, from #92: the media-stack step used `flatpak-builder --run`, which needs rofiles-fuse and fails in the Flathub image (no machine-id), and `--run` rejects `--disable-rofiles-fuse`. It now uses `flatpak build builddir`, verified in the `gnome-51` image (`avdec_h264` present there). (2) `media quit`, from #93: its GIFV came from a plain `http.server` with no Range. Fedora's GStreamer 1.28.9 refuses that server ("Server does not accept Range HTTP header"), so the clip never prerolled; local 1.28.8 plays the same clip 3/3. Fixed by switching to the Range-capable `MediaServer`; the unused `serve()` was removed and the failure detail now shows the child's `QUIT` line. Both failures reproduced and fixed in containers before pushing. Later runs found three more: (3) `media quit` on #95+ hung at startup in `Gio.NetworkMonitor.get_default()` (sysstate, first built by the loop gate; `GIO_USE_NETWORK_MONITOR=base` did not help). The child now gets a stand-in monitor, found from a `faulthandler` dump the test now takes. (4) `media engine` "RSS plateaus" compared endpoints while RSS alternated 489/524 MB; it now compares peaks. (5) `web column` timed out on main too (control run 42807218607): `web_test_env` lacked the suite's `GSK_RENDERER=cairo`/`LANG` pins, and rawhide's Mesa (Zink) hangs the GL path. Fixed on main in PR #97 (main 3/3 timeouts → 3/3 OK in the CI image) and merged into the stack. Merging current main into #96 also brought #91's catalogues; the `.po` conflicts were resolved as a union with no translation lost. **CI green on #92–#97 (2026-11-10)**; #94 needed one rerun for `nareal plugin` (main's test, ~60 s of a 120 s limit on every run and locally, a separate follow-up) |
| Remote media consent (PR #96, `feat/remote-media-consent`, stacked on #95) | ✅ accepted 2026-11-09: option B, `remote-media` ask/load/never, asked only when the instance has no cached copy of the media; User-Agent teardown race fixed (0/30, was ~1 in 10). Suite 99/0/1, crash 250/250, quit 20/20. Logs: `reports/video-playback-bench/qa-consent/` |
| Phase 1d (PR #95, `feat/viewer-motion-volume`, stacked on #94; one PR, 5 commits) | ✅ accepted 2026-11-09: live reduced-motion/animations/power-saver gate decided at prepare, transition-only; viewer volume remembered (`media-viewer-volume`, near-zero not saved); audio state pushed to a reused engine; graphics offload on the viewer's `Gtk.Video`. Suite 98/0/1, crash 250/250, quit 20/20. Logs: `reports/video-playback-bench/qa1d/` |
| Phase 1c (PR #94, `refactor/viewer-split`, stacked on #93) | ✅ accepted 2026-11-09: `mediaviewer.py` 1064 → 514 lines (`mediaexport.py` 302, `mediaviewerchrome.py` 316), off `EXCEPTIONS`; all 69 moved function bodies identical by `ast.dump`; review caught a dropped `add_controller(keys)` the suite missed, now covered by a key-press test shown to fail without it; suite 98/0/1, crash 250/250, quit 20/20. Logs: `reports/video-playback-bench/qa1c/` |
| Phase 1b (PR #93, `feat/viewer-streaming`, stacked on #92) | ✅ accepted 2026-11-09: streaming crash gate 250/250 (+500/500), quit 20/20 (+50/50), `media progressive` 8/8, sandbox streams with Babble's User-Agent; full suite 97/1/1 where the failure was `reading position` (untouched, 5/5 on rerun). No download fallback, by decision. Logs: `reports/video-playback-bench/qa1b/` |
| Phase 1a (PR #92, `fix/viewer-gstplay-engine`) | ✅ accepted 2026-11-09 on the final tree: full suite 97 OK / 0 failed / 1 expected skip; crash gate 250/250 real-viewer cycles, 0 crashes (plus 2 × 500/500 on the two earlier fix rounds); quit while playing 20/20 (plus 2 × 50/50 earlier); 0 python core dumps; quit test shown able to fail (mutation: 2/10 crash with `shutdown_all` disabled). Logs: `reports/video-playback-bench/qa/` |

Next:
1. Phase 1a as an urgent crash fix: per-viewer engine, deferred release, quit-while-playing handling, still on downloaded files. Its acceptance test is `e3b_viewer.py`'s 500 cycles with 0 crashes, turned into `test-mediaviewer-teardown.py`.
2. Phase 1a also takes from E6: `--socket=pulseaudio` in the local dev manifest; `seekable` from media info; the codec error naming `codecs-extra` for H.264/HEVC; lazy engine creation.
