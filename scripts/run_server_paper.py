        "status": s["status"], "updated_at": s["updated_at"],
        "run_started_at": s.get("run_started_at"), "run_finished_at": s.get("run_finished_at"),
        "day_ist": s["day_ist"], "capital": 100000.0, "cash": s["cash"],
        "realized_pnl": s["realized_pnl"], "trades_today": s["trades_today"],
        "max_trades_per_day": MAX_TRADES_PER_DAY, "max_daily_loss_rs": MAX_DAILY_LOSS_RS, "max_equity_drawdown_rs": MAX_DAILY_LOSS_RS,
        "pairs": len(pairs), "pair_list": pairs, "events": s["events"],
        "signals": s["signals"], "accepted_signals": s["accepted_signals"],
        "rejected_signals": s["rejected_signals"], "errors": s["errors"],
        "position": s["position"], "browser_required": False,
        "real_orders": False, "config": CFG,
    }
    save_state(STATE_PATH, s)
    save_state(SUMMARY_PATH, summary)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()