frappe.ui.form.on("Integration Log Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Backfill now"), () => {
			frappe.call({
				method: "integration_log.api.start_backfill",
				freeze: true,
				callback(r) {
					const started = r.message || [];
					frappe.show_alert({
						message: started.length
							? __("Backfill started in the background.")
							: __("Backfill is already queued or running."),
						indicator: started.length ? "green" : "orange",
					});
				},
			});
		});
	},
});
