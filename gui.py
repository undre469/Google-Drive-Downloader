import customtkinter as ctk
from tkinter import filedialog, messagebox
import threading
import asyncio
import sys
from main import mirror_drive_async


class DriveMirrorApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        # --- Window setup ---
        self.title("Google Drive Mirror")
        self.geometry("760x600")
        ctk.set_appearance_mode("system")  # Auto-switch light/dark
        ctk.set_default_color_theme("blue")

        self.total_files = 0
        self.completed = 0

        # --- Fonts ---
        self.font_title = ctk.CTkFont("SF Pro Display", 22, "bold")
        self.font_label = ctk.CTkFont("SF Pro Display", 13)
        self.font_small = ctk.CTkFont("SF Pro Display", 12)

        # --- UI ---
        self.create_widgets()

    # -------------------- UI LAYOUT -------------------- #
    def create_widgets(self):
        # Header section
        header = ctk.CTkFrame(self, fg_color=("gray95", "gray12"), corner_radius=20)
        header.pack(fill="x", pady=(15, 10), padx=20)
        ctk.CTkLabel(header, text="Google Drive Mirror", font=self.font_title).pack(pady=(10, 0))
        ctk.CTkLabel(header, text="Mirror your Drive and Shared files locally", font=self.font_small).pack(pady=(0, 10))

        # Body section
        body = ctk.CTkFrame(self, corner_radius=20)
        body.pack(fill="x", padx=25, pady=(0, 15))

        # Credentials
        ctk.CTkLabel(body, text="Credentials JSON:", font=self.font_label).pack(anchor="w", padx=15, pady=(15, 3))
        creds_row = ctk.CTkFrame(body, fg_color="transparent")
        creds_row.pack(fill="x", padx=15)
        self.credentials_entry = ctk.CTkEntry(creds_row, placeholder_text="Select credentials.json")
        self.credentials_entry.pack(side="left", fill="x", expand=True, pady=4)
        ctk.CTkButton(creds_row, text="Browse", width=100, command=self.select_credentials).pack(side="right", padx=(8, 0))

        # Output folder
        ctk.CTkLabel(body, text="Output Folder:", font=self.font_label).pack(anchor="w", padx=15, pady=(12, 3))
        out_row = ctk.CTkFrame(body, fg_color="transparent")
        out_row.pack(fill="x", padx=15, pady=(0, 15))
        self.output_entry = ctk.CTkEntry(out_row, placeholder_text="Choose destination folder")
        self.output_entry.pack(side="left", fill="x", expand=True, pady=4)
        ctk.CTkButton(out_row, text="Browse", width=100, command=self.select_output).pack(side="right", padx=(8, 0))

        # Start button
        self.start_button = ctk.CTkButton(
            self, text="Start Mirror", corner_radius=25, height=44,
            font=("SF Pro Display", 15, "bold"), command=self.start_mirror
        )
        self.start_button.pack(pady=(10, 10))

        # Progress
        self.progress_label = ctk.CTkLabel(self, text="Idle", font=self.font_small)
        self.progress_label.pack(pady=(8, 2))

        self.progress = ctk.CTkProgressBar(self, width=520, height=14, corner_radius=10)
        self.progress.set(0)
        self.progress.pack(pady=(0, 18))

        # Download log list
        self.list_frame = ctk.CTkFrame(self, corner_radius=20)
        self.list_frame.pack(fill="both", expand=True, padx=25, pady=(0, 20))
        ctk.CTkLabel(self.list_frame, text="Downloaded Files", font=self.font_label).pack(anchor="w", padx=15, pady=(10, 2))

        self.list_box = ctk.CTkTextbox(self.list_frame, height=240, corner_radius=15)
        self.list_box.pack(fill="both", expand=True, padx=15, pady=(5, 15))
        self.list_box.configure(state="disabled")

    # -------------------- FILE PICKERS -------------------- #
    def select_credentials(self):
        path = filedialog.askopenfilename(title="Select credentials.json", filetypes=[("JSON Files", "*.json")])
        if path:
            self.credentials_entry.delete(0, "end")
            self.credentials_entry.insert(0, path)

    def select_output(self):
        path = filedialog.askdirectory(title="Select Output Folder")
        if path:
            self.output_entry.delete(0, "end")
            self.output_entry.insert(0, path)

    # -------------------- MIRROR TASK -------------------- #
    def start_mirror(self):
        creds = self.credentials_entry.get().strip()
        output = self.output_entry.get().strip()
        if not creds or not output:
            messagebox.showerror("Error", "Please select both credentials and output folder.")
            return

        self.start_button.configure(state="disabled", text="Running...")
        self.progress_label.configure(text="Connecting to Google Drive…")
        self.progress.set(0)
        self.clear_list()

        # Run in a thread to avoid blocking the mainloop
        threading.Thread(target=self.run_async_mirror, args=(creds, output), daemon=True).start()

    def run_async_mirror(self, creds, output):
        try:
            asyncio.run(self._async_mirror_task(creds, output))
            self.update_progress(1, 1, "")
            self.progress_label.configure(text="Completed successfully!")
        except Exception as e:
            self.add_list_item(f"ERROR: {e}")
            self.progress_label.configure(text=f"Error occurred: {e}")
        finally:
            self.start_button.configure(state="normal", text="Start Mirror")

    async def _async_mirror_task(self, creds, output):
        async for update in mirror_drive_async(creds, output, progress_callback=self.update_progress):
            self.add_list_item(update)

    # -------------------- UI UPDATES -------------------- #
    def update_progress(self, done, total, current_file):
        try:
            if total == 0:
                return
            progress = done / total
            percent = progress * 100
            self.progress.set(progress)
            label_text = f"{done}/{total} • {percent:.1f}%"
            if current_file:
                label_text += f" — {current_file}"
            self.progress_label.configure(text=label_text)
            self.update_idletasks()
        except Exception:
            pass

    def add_list_item(self, msg):
        self.list_box.configure(state="normal")
        self.list_box.insert("end", msg + "\n")
        self.list_box.see("end")
        self.list_box.configure(state="disabled")

    def clear_list(self):
        self.list_box.configure(state="normal")
        self.list_box.delete("1.0", "end")
        self.list_box.configure(state="disabled")


if __name__ == "__main__":
    app = DriveMirrorApp()
    app.mainloop()
