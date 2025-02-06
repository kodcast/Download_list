from pynicotine.pluginsystem import BasePlugin
from gi.repository import Gtk, GLib
from pynicotine.core import core
from pynicotine.events import events
from pynicotine.slskmessages import FileListMessage
import re
import gettext
import os

# Setup localization
LOCALE_DIR = os.path.join(os.path.dirname(__file__), "locales")
gettext.bindtextdomain("download_list", LOCALE_DIR)
gettext.textdomain("download_list")
_ = gettext.gettext

def normalize_quality(q):
    """Normalizes a quality string by removing spaces and converting to lowercase."""
    return re.sub(r'\s+', '', q.lower())

class Plugin(BasePlugin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.log(_("🔄 Loading Download by List plugin..."))
        # Flag indicating that a download has been launched for the current search term.
        self.download_launched = False
        # List of search terms and current index.
        self.search_terms = []
        self.current_search_index = 0
        # Current pending search term awaiting response.
        self.current_pending_term = ""
        # Timer ID for the current term.
        self.current_timeout = None
        # Timeout (in seconds) to wait for a search response.
        self.response_timeout = 5
        # Delay (in seconds) between detecting a result and launching the download.
        self.download_delay = 3
        # Set (without duplicates) of terms for which no file was found.
        self.missing_search_terms = set()

    def loaded_notification(self):
        """Called when the plugin is loaded."""
        self.log(_("🔔 Plugin loaded."))
        events.connect("file-search-response", self.file_search_response)
        self.show_window()

    def show_window(self):
        """Creates a window with search options and a scrollable area for the final results."""
        self.log(_("🔧 Creating main window..."))

        self.window = Gtk.Window(title=_("Download by List"))
        self.window.set_default_size(400, 450)
        self.window.connect("destroy", self.on_window_destroy)

        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.widget.set_margin_top(10)
        self.widget.set_margin_bottom(10)
        self.widget.set_margin_start(10)
        self.widget.set_margin_end(10)
        self.window.set_child(self.widget)

        # Title label
        title_label = Gtk.Label(label=_("Enter search terms below:"))
        title_label.set_xalign(0.5)
        title_label.set_halign(Gtk.Align.CENTER)
        self.widget.append(title_label)

        # Text input area (TextView in a ScrolledWindow)
        search_scrolled = Gtk.ScrolledWindow()
        search_scrolled.set_size_request(-1, 200)
        self.text_view = Gtk.TextView()
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.text_buffer = self.text_view.get_buffer()
        search_scrolled.set_child(self.text_view)
        self.widget.append(search_scrolled)

        # Audio format selection
        format_label = Gtk.Label(label=_("Audio Format:"))
        format_label.set_xalign(0)
        self.widget.append(format_label)
        self.format_combo = Gtk.ComboBoxText()
        for fmt in ["MP3", "FLAC", "OGG", "OPUS", "WAV"]:
            self.format_combo.append_text(fmt)
        self.format_combo.set_active(0)
        self.widget.append(self.format_combo)

        # Audio quality selection
        quality_label = Gtk.Label(label=_("Audio Quality:"))
        quality_label.set_xalign(0)
        self.widget.append(quality_label)
        self.quality_combo = Gtk.ComboBoxText()
        for quality in ["320kbps", "192kbps", "128kbps", "44.1 KHz/16 bit", ""]:
            self.quality_combo.append_text(quality)
        self.quality_combo.set_active(0)
        self.widget.append(self.quality_combo)

        # Button to start search and download
        apply_button = Gtk.Button(label=_("Search and Download"))
        apply_button.connect("clicked", self.on_apply_button_clicked)
        self.widget.append(apply_button)

        # Scrollable area for displaying final messages (e.g. terms not found)
        final_scrolled = Gtk.ScrolledWindow()
        final_scrolled.set_size_request(-1, 100)  # Fixed height
        self.final_message_view = Gtk.TextView()
        self.final_message_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.final_message_view.set_editable(False)
        self.final_message_view.set_cursor_visible(False)
        self.final_message_buffer = self.final_message_view.get_buffer()
        final_scrolled.set_child(self.final_message_view)
        self.widget.append(final_scrolled)

        self.window.present()

    def on_window_destroy(self, window):
        """Closes the window properly."""
        self.log(_("❌ Window closed."))
        window.destroy()

    def set_final_message(self, message):
        """Sets the text in the final message area."""
        self.final_message_buffer.set_text(message)

    def on_apply_button_clicked(self, button):
        """Resets variables and starts processing the search terms."""
        self.log(_("🖱️ 'Search and Download' button clicked!"))
        self.download_launched = False
        self.current_search_index = 0
        self.missing_search_terms = set()
        self.current_timeout = None
        self.set_final_message("")
        text_content = self.text_buffer.get_text(
            self.text_buffer.get_start_iter(),
            self.text_buffer.get_end_iter(),
            True
        ).strip()
        if not text_content:
            self.log(_("⚠️ No valid content in the text area."))
            self.set_final_message(_("⚠️ Please enter at least one search term."))
            return
        self.search_terms = [term.strip() for term in text_content.splitlines() if term.strip()]
        if not self.search_terms:
            self.log(_("⚠️ No valid search terms found."))
            self.set_final_message(_("⚠️ No valid search terms found."))
            return
        self.log(_("🔍 Starting searches for {count} term(s)").format(count=len(self.search_terms)))
        self.schedule_next_search()

    def schedule_next_search(self):
        """
        Processes the current term and then schedules checking and moving to the next term.
        """
        if self.current_search_index >= len(self.search_terms):
            if self.missing_search_terms:
                message = _("❌ No file found for:\n ") + "\n ".join(sorted(self.missing_search_terms))
            else:
                message = _("✅ All files have been found.")
            self.set_final_message(message)
            self.log(message)
            return

        # Process the current term.
        term = self.search_terms[self.current_search_index]
        self.current_pending_term = term
        self.download_launched = False

        self.log(_("📡 Searching for: {term}").format(term=term))
        try:
            core.search.do_search(term, mode="global")
        except Exception as e:
            self.log(_("❌ Error during search for '{term}': {error}").format(term=term, error=e))
            self.missing_search_terms.add(term)
            self.current_search_index += 1
            GLib.idle_add(self.schedule_next_search)
            return

        # Start a timer that will call process_current_search if no result is found.
        self.current_timeout = GLib.timeout_add_seconds(self.response_timeout, self.process_current_search, term)
        self.current_search_index += 1

    def process_current_search(self, term):
        """
        Checks if a result has been found for the given term.
        If no download was launched, adds the term to the missing list and processes the next term.
        """
        if term == self.current_pending_term and not self.download_launched:
            self.log(_("❌ No matching file found for {term}").format(term=term))
            self.missing_search_terms.add(term)
        self.current_timeout = None
        self.schedule_next_search()
        return False

    def file_search_response(self, response):
        """
        Processes the search response. If a matching result is found for the current term
        (stored in self.current_pending_term), launches the download.
        """
        if self.download_launched:
            return

        self.log(_("📩 Received search results..."))
        result_list = getattr(response, "list", None)
        if result_list is None:
            return

        selected_format = self.format_combo.get_active_text().lower()
        selected_quality = self.quality_combo.get_active_text()
        user = getattr(response, "username", "Unknown")
        found_match = False

        for result in result_list:
            try:
                _code, file_path, size, _ext, file_attributes, *rest = result
            except Exception as e:
                self.log(_("❌ Error extracting tuple: {error}").format(error=e))
                continue

            filename = file_path.split("\\")[-1]
            h_format = filename.split(".")[-1].lower()
            h_quality, bitrate, h_length, length = FileListMessage.parse_audio_quality_length(size, file_attributes)
            if not h_quality and file_attributes and isinstance(file_attributes, tuple):
                try:
                    bitrate_val = int(file_attributes[0])
                    h_quality = f"{int(bitrate_val/1000)}kbps"
                except Exception as e:
                    self.log(_("❌ Error converting bitrate: {error}").format(error=e))
                    h_quality = ""
            else:
                h_quality = h_quality or ""

            # Normalize strings for reliable comparison.
            quality_match = normalize_quality(selected_quality) == normalize_quality(h_quality) if h_quality else False
            format_match = (h_format == selected_format)
            is_private = "[prive]" in filename.lower()

            self.log(_("🎯 Checking file: {filename} (Format: {h_format}, Quality: {h_quality}, Format match: {format_match}, Quality match: {quality_match}, private: {is_private})").format(
                filename=filename, h_format=h_format, h_quality=h_quality,
                format_match=format_match, quality_match=quality_match, is_private=is_private))
            if format_match and quality_match and not is_private:
                found_match = True
                self.log(_("✅ Matching result found for {term}: {filename}").format(term=self.current_pending_term, filename=filename))
                # Cancel the timer for this term.
                if self.current_timeout is not None:
                    GLib.source_remove(self.current_timeout)
                    self.current_timeout = None
                # Schedule download after a delay, then process the next term.
                GLib.timeout_add_seconds(self.download_delay, self.delayed_download, user, file_path)
                self.download_launched = True
                break

        if not found_match:
            self.log(_("❌ No matching file found for {term}").format(term=self.current_pending_term))
            # If no match is detected, process_current_search (triggered by timer) will handle the term.

    def delayed_download(self, user, file_path):
        """
        Launches the download after a delay, then processes the next term.
        """
        try:
            core.downloads.enqueue_download(user, file_path)
            self.log(_("🚀 Download launched for: {file}").format(file=file_path))
        except Exception as e:
            self.log(_("❌ Error downloading {file}: {error}").format(file=file_path, error=e))
        self.schedule_next_search()
        return False

    def log(self, message):
        """Logs a message in Nicotine+ logs."""
        print(f"[Download List] {message}")

