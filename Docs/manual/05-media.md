# 5 — Media: attaching, viewing, saving

> **Document:** `BABBLE-MAN-001` — chapter 5 — v1.0 — approved
> **Software described:** Babble 0.3.0 · **Date:** 2026-11-11 · **Parent:** [The Babble User Manual](README.md)

Media is everything that is not text: images, videos, animated GIFs and audio. This chapter runs from "how do I attach this" to "how do I save that", and passes through the decision Babble takes on your behalf when the media is not on your server.

## 5.1 Four ways to attach media

**Objective:** put an image, a video or an audio file into the post.

**Pre-conditions:** the composer is open.

1. Choose one of the four routes:
   - **`Attach Media`** (clip icon, in the bottom bar) opens the file chooser. The filter offered depends on the server: `Media accepted by this server` when the server declares what it accepts, `Images, Videos and Audio` otherwise.
   - **Drag and drop**: drag the files from a file manager into the composer.
   - **Paste**: with the cursor in the text field, **Ctrl+V** pastes copied files. A single image on the clipboard becomes a PNG in the cache, named `Pasted image`.
   - **`Take Screenshot`** (camera icon, in the bottom bar) takes a screenshot through the XDG portal and attaches it. The button only appears when that portal exists on the system.

**Result:** each attachment becomes a row in the composer, with a thumbnail, the file name, a remove button (`Remove Attachment`) and a `Description` field.

**If it goes wrong:**

| Message | Cause |
|---|---|
| `Only images, videos and audio can be attached` | The file is not an image, a video or an audio file. |
| `A post can have at most {n} attachments` | The server limits how many there can be (4 by default). |
| `A video or audio file must be the only attachment` | A video or an audio file has to be the only attachment. |
| `Wait for the post to be sent to attach files` | A post is already in progress. |
| `Could not open the file picker` | The file chooser did not open. |
| `Could not paste the image` / `Could not paste the files` | The clipboard had nothing usable in it. |
| `Could not take a screenshot` | The portal refused the capture. |

## 5.2 The media description

Every attachment has a `Description` field. It is the alternative text: what Babble announces to a screen reader, and what serves people who cannot see the image. It is sent to the server as the media description, and it is what the server shows to your followers.

The limit comes from the server (`description_limit`). Babble does not require you to fill it in — but **without a description, anyone using a screen reader does not know what is in the post**, and that is why Babble asks before letting you boost a post with media that has no description (see [2.4](02-reading.md#before-you-boost)).

## 5.3 The upload

When you post, each attachment is uploaded to the server. Before that, Babble checks the file size against the server's limit; if it goes over, the refusal is explicit:

> `“{name}” is too large for this server ({limit} is the limit)`

After the upload, the server may need time to process the file (that is what happens with video). Babble waits for that processing and says:

| Message | Meaning |
|---|---|
| `The server is still processing the media` | The server is still working; Babble waits up to two minutes. |
| `The server did not accept the media` | The server refused, or took too long. |
| `Could not upload media` | The upload failed. |

GIFs are uploaded as `image/gif`; it is the server that transcodes them to MP4. Babble never uploads the `gifv` format.

Removing an attachment shows an `Attachment removed` toast with `Undo` — you can bring it back.

## 5.4 The media viewer

The viewer **is not a separate window**: it covers the main window, which is why the `Fullscreen` button really fills the screen.

### The header

The title gives the type of the attachment: `Image`, `Video`, `Animated GIF`, `Audio` or `Attachment`. With more than one attachment, a subtitle shows `{current} of {total}`.

The buttons are `Close`, `Fullscreen` (which becomes `Exit Fullscreen`), `Save As…` and a `More Actions` menu:

| Item | What it does |
|---|---|
| `Copy URL` | Copies the file address. |
| `Copy Image` | Copies the image to the clipboard. Images only. |
| `Open in Browser` | Opens the file in the browser. |
| `Open With…` | Downloads the file and hands it to the installed applications. |

### The stage

- `Previous Attachment` and `Next Attachment` move between attachments. A row of dots shows the position: `Attachment {n} of {total}`.
- The zoom, for images: `Zoom Out`, `Zoom In`, `Fit to Window` and a button showing the current percentage, whose tooltip is `Toggle Fit and Actual Size`.

### The keyboard

| Key | What it does |
|---|---|
| **←** / **→** | Goes to the previous or the next attachment. |
| **F** | Toggles full screen. |
| **Esc** | Closes the viewer. |
| **+** / **-** | Zooms in and out. |
| **0** | Fits to the window. |

### The description

Below the media there is an `ALT` button, whose tooltip is `Show or Hide Description`. The text is selectable, which lets you copy it.

### When the media does not open

| Message | Cause |
|---|---|
| `No Media` | The post has no media. |
| `Media Still Processing` | The server is still processing the file: *"Open the post again in a moment."* |
| `Preview Not Available` | This kind of attachment cannot be shown here. |
| `Could Not Load Image` | The image did not load. |
| `Could Not Load Media` | *"Playback is not available on this system."* |

When it fails, there are the `_Try Again` and `_Open in Browser` buttons.

## 5.5 Zoom and drag

The zoom starts at "fit to window". Each step multiplies by 1.25, and the limit is 8× (or more, for small images).

- **Ctrl + mouse wheel** zooms in; the wheel without Ctrl scrolls the image.
- **Pinch** zooms on touch screens.
- **Double click** toggles between "fit to window" and the actual size (200% for small images).
- **Dragging** an image that fits in the window takes it out of the window — it goes to another application as a file, together with its URL. With the zoom applied, dragging scrolls the image instead of extracting it.

To extract video or audio, what goes out is the downloaded file or its address.

## 5.6 Saving media

**Objective:** keep the file on your computer.

**Pre-conditions:** the media is open in the viewer, and you have write permission in the destination folder.

1. Open the media in the viewer.
2. Click `Save As…`.
3. Choose the destination. The dialog is called `Save Media` and the button is `_Save`. The suggested name comes from the file address; without one, it is `media` with the extension that matches the type.

**Result:** a `Saved “{name}”` toast appears, with a `Show in Folder` button that opens the file manager in the right place. While the download runs, a progress bar sits in the viewer.

**If it goes wrong:** `Could not save media`.

## 5.7 Media hosted on another server

This is the point where the federation charges its toll, and it is worth understanding before you need it.

When your server shows a post with media, it keeps a **copy** of the media. That copy is what Babble displays, and there is no cost to you or to the originating server.

But there are cases where your server has **no** copy — usually when the originating server is out of your server's reach, or the media was removed from there. In those cases, the file exists only on the originating server. Loading it straight from there means that **the originating server sees your IP address and knows that you opened that post**.

That is why Babble asks. The `Load Media From Another Server?` dialog says:

> *"Your server has no copy of this media. Loading it from {host} lets that server see your IP address and that you viewed it."*

The buttons are `_Cancel` and `_Load`, and there is a `_Remember This Choice` checkbox so that it does not ask again.

### The setting

Preferences → Reading → `Media` group → **`Media From Other Servers`**:

| Value | What happens |
|---|---|
| `Ask Each Time` | Asks for each piece of media. This is the default. |
| `Always Load` | Loads without asking. |
| `Never Load` | Does not load; the viewer offers to open it in the browser. |

Media that **your server has** never raises this question.

If you decline, the viewer shows `Media Not Stored by Your Server` — *"Your server has no copy of this media. Open it in the browser to view it on the original server."* — and, when the setting is `Never Load`, a toast warns `Loading media from other servers is turned off`.

**What is never blocked:** opening the original in the browser, copying the address and dragging the media out make no request to the originating server. The consent covers only the *loading* inside the application.

## 5.8 Autoplay and loading

Three preferences in the `Media` group in Preferences → Reading decide when heavy media starts downloading and playing.

### `Load Media Automatically`

It covers video, audio and animated GIFs — which are streamed, not merely displayed. Images and thumbnails always load.

| Value | What happens |
|---|---|
| `Always` | Loads everything straight away. |
| `Not on Metered Connections` | The default. Loads, unless the system marks the connection as metered. |
| `Never` | Waits for you to ask. |

When the media does not load by itself, a `Load Video` or `Load Audio` button appears, with the tooltip `Media is not downloaded automatically on a metered connection` (or `You chose not to load media automatically`).

### `Autoplay Animated Media`

It comes on. With it on, an animated GIF plays in a loop, without sound. With it off, the GIF stays still until you click it.

Three system settings **turn autoplay off**, even with the preference on:

- reduced motion requested by the system;
- animations turned off in GNOME;
- power saving mode.

These three conditions are re-evaluated **while the GIF plays** — if the system enters power saving halfway through, the playback stops.

The preference does not affect video with sound.

### `Play Animated GIFs in Timelines`

It comes **off**, and it is a deliberate choice: the effect costs more memory than a still image, and the window may pause for an instant on the first playback.

When it is on, the **most visible** GIF on the page is the only one that plays, and the playback happens in place, inside the timeline. A button over the GIF toggles: `Pause Animation` while it plays, `Play Animation` when it is paused. A GIF you paused stays paused on the next scroll.

The three restrictions above still apply, as do `Load Media Automatically` and `Media From Other Servers`.

The volume of the video and of the audio is remembered between sessions.

## 5.9 Sensitive media

A post can mark its media as sensitive. The media then appears covered, and the `Show Sensitive Content` button reveals it; once revealed, the button becomes `Hide Sensitive Content`.

The **`Sensitive Media`** preference (`show-sensitive-media`) reveals the media automatically, with no button. Anyone who prefers to decide post by post turns it off.

## 5.10 Link preview cards

When a post has a link, the server may return a **preview** — an image, a title and a description of a page. Babble shows that card below the post. The **`Link Preview Cards`** preference turns the display off.

The preview card also appears in the trending news of `Explore`, and the `News` tab shows who is talking about the link: `By {source}`, `{n} person talking` / `{n} people talking`, or `Shared by {account}`.
