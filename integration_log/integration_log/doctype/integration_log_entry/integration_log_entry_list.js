frappe.listview_settings["Integration Log Entry"] = {
	filters: [["state", "=", "Open"]],
	add_fields: ["state", "severity"],

	get_indicator(doc) {
		if (doc.state === "Resolved") {
			return [__("Resolved"), "green", "state,=,Resolved"];
		}
		if (doc.state === "Acknowledged") {
			return [__("Acknowledged"), "gray", "state,=,Acknowledged"];
		}
		const colors = { error: "red", warning: "orange", info: "blue" };
		return [__(frappe.utils.to_title_case(doc.severity || "info")), colors[doc.severity] || "blue", "severity,=," + doc.severity];
	},

	onload(listview) {
		const run = (method, args, done) => {
			frappe.call({
				method: "integration_log.api." + method,
				args,
				freeze: true,
				callback(r) {
					frappe.show_alert({ message: __("{0} entries updated", [r.message || 0]), indicator: "green" });
					done && done();
					listview.refresh();
				},
			});
		};

		listview.page.add_actions_menu_item(__("Acknowledge"), () => {
			const names = listview.get_checked_items(true);
			if (!names.length) return;
			frappe.prompt(
				{ fieldname: "note", fieldtype: "Small Text", label: __("Note") },
				(values) => run("acknowledge", { names, note: values.note || null }),
				__("Acknowledge"),
				__("Acknowledge")
			);
		});

		listview.page.add_actions_menu_item(__("Reopen"), () => {
			const names = listview.get_checked_items(true);
			if (!names.length) return;
			run("reopen", { names });
		});
	},
};
