# WritersBot: what to set up on your Windows computer

WritersBot itself runs on the Orin. Your Windows computer does two things: it shows WritersBot's window, and it makes
the pictures. Here is what to download and do, in order. Nothing here touches the Orin.

## Part 1. The WritersBot window (needed)

The window is WritersBot's own desktop app. It talks to the Orin over your home network.

This computer has to be on the same home network as the Orin.

**Install the window**

1. Ask the person at the Orin for its address. It looks like `http://10.0.0.25:8423`.
2. On the WritersBotFounders page on GitHub, press "Code", then "Download ZIP". Right-click the zip file you get, choose
   **Extract All**, and open the new folder.
3. Double-click **Get the WritersBot window**. If Windows says it protected your PC, press "More info", then "Run anyway".
4. Type the address when it asks. It gets Python and Microsoft's WebView2 if this computer needs them, which can take a
   few minutes, and then opens the window.
5. The window asks for a sign-in code. Whoever is at the Orin runs `./install-writersbot.sh pair` and reads you the code.
   Type it once. The window remembers you after that.

**Opening it from now on:** double-click **WritersBot** on your desktop. You can delete the zip and the folder you
extracted. If the window ever opens on the sign-in screen again, ask for a new code. If the Orin's address changes, run
**Get the WritersBot window** again with the new one.

**Inside the window:** chat in the middle, your apps on the left (Manuscript, Maps and the rest), and the side pane on
the right where apps open. Say "start novel mode" in the chat to begin.

## Part 2. Pictures (optional, but worth it)

Maps makes portraits, places, painted world maps and cover mock-ups through a picture program called ComfyUI that
runs on your Windows computer. Your computer does the drawing; the Orin only sends it a short description built from
the story bible, never your text.

**You need a graphics card.** An NVIDIA card with 8 GB of memory or more is what works well; the model below makes a
picture in about 30 seconds on one. With a smaller card, or no NVIDIA card, skip this part for now; it can run on the
Orin itself later instead.

**Download**

1. **ComfyUI, Windows portable.** From the ComfyUI page on GitHub (comfyanonymous/ComfyUI), under Releases, the file
   named like `ComfyUI_windows_portable_nvidia.7z`. Unzip it to `C:\ComfyUI` (you may need 7-Zip, free, from 7-zip.org).
   It carries its own Python; it does not use the one from Part 1.
2. **The GGUF loader add-on.** In `C:\ComfyUI\ComfyUI\custom_nodes`, put the folder `ComfyUI-GGUF` from GitHub
   (city96/ComfyUI-GGUF, "Code", "Download ZIP", unzip, rename the folder to `ComfyUI-GGUF`).
3. **Four model files**, about 12 GB in all, into these folders under `C:\ComfyUI\ComfyUI\models`:

| File | Goes in | Size | Where from |
|---|---|---|---|
| `flux1-schnell-Q4_K_S.gguf` | `unet` | 6.4 GB | huggingface.co, city96/FLUX.1-schnell-gguf |
| `t5xxl_fp8_e4m3fn.safetensors` | `clip` | 4.6 GB | huggingface.co, comfyanonymous/flux_text_encoders |
| `clip_l.safetensors` | `clip` | 235 MB | huggingface.co, comfyanonymous/flux_text_encoders |
| `ae.safetensors` | `vae` | 320 MB | huggingface.co, Comfy-Org/Lumina_Image_2.0_Repackaged, in `split_files/vae` (the `ae.safetensors` file; the copy at black-forest-labs needs a sign-in) |

**Start it**

1. In `C:\ComfyUI`, open `run_nvidia_gpu.bat` in Notepad and add ` --listen 0.0.0.0` at the end of the line that
   starts the program, so the Orin can reach it. Save. Then double-click `run_nvidia_gpu.bat`. A black window stays
   open; that is ComfyUI running. Leave it open while you make pictures.
2. **The first time, Windows asks "Allow this app to communicate on these networks?" for Python. Tick both boxes and
   press Allow.** If you press Cancel by mistake, pictures will not work; the fix is in Windows Security, Firewall,
   Allow an app through firewall, find python and tick it.
3. Find your computer's address: in PowerShell type `ipconfig` and read the "IPv4 Address" of your Wi-Fi or Ethernet,
   like `10.0.0.30`.

**Tell Maps about it, once**

1. In the WritersBot window, open Maps from the left.
2. In the **Picture maker** box type `http://10.0.0.30:8188` with your own address, and press **Test**. It should say it
   reached the picture program and will use FLUX schnell.
3. A Yes card appears on the Bridge page asking to allow that one address. Say Yes.
4. Open Pictures for your manuscript, pick a character, press **Make**. The first picture takes about a minute while
   your card loads the model; after that, under half a minute each.

## If something does not work

- **The window says it cannot reach the bot:** check the address, and that the Orin and your computer are on the same
  home network.
- **The window wants a code again:** ask for a fresh `puffpai pair` on the Orin. Codes work once and for ten minutes.
- **Maps' Test says it cannot reach the picture program:** ComfyUI's black window must be open; the Allow popup must
  have been accepted; and the address must be your computer's, not the Orin's.
- **Pictures fail with "no model":** check the four files are in the right folders under `models`, and that
  `ComfyUI-GGUF` is in `custom_nodes`. Restart ComfyUI after adding files.
