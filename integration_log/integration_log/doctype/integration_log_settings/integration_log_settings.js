frappe.ui.form.on("Integration Log Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Backfill now"), () => {
			frappe.call({
				method: "integration_log.api.start_backfill",
				freeze: true,
				callback(r) {
					const result = r.message || {};
					const started = result.enqueued || [];
					const queued = result.already_queued || [];
					let message = __("No enabled sources to backfill.");
					if (started.length) {
						message = __("Backfill started in the background.");
					} else if (queued.length) {
						message = __("Backfill is already queued or running.");
					}
					frappe.show_alert({
						message: message,
						indicator: started.length ? "green" : "orange",
					});
				},
			});
		});
	},
});
