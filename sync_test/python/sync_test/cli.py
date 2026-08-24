import argparse
import sys
import logging

def main():
    parser = argparse.ArgumentParser(description="Automated UI Sync Test Runner")
    parser.add_argument("command", choices=["run", "status"], help="Command to execute: 'run' executes tests, 'status' shows each test's last recorded result without running anything")
    parser.add_argument("--config", default="sync_tests.yaml", help="Path to test suite configuration file")
    parser.add_argument("--test", nargs="+", metavar="TEST", help="Limit to specific test(s) by name (space-separated for multiple)")
    parser.add_argument("--script-driven", action="store_true", help="Run the test using script-driven UI commands instead of JSONL replay")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        # Milliseconds matter: runner events are correlated by hand against the
        # app/plugin logs, which timestamp to the millisecond. At whole-second
        # resolution a seek, the broadcast it triggers and the state read that
        # checks it all collapse onto the same instant, hiding the ordering
        # that is usually the whole question.
        format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S"
    )
    
    from .runner import TestRunner
    try:
        runner = TestRunner(args.config)
    except Exception as e:
        logging.error(f"Configuration error: {e}")
        sys.exit(1)

    if args.command == "status":
        runner.print_status(test_names=args.test)
        sys.exit(0)

    if args.command == "run":
        logging.info(f"Starting test runner with config: {args.config}")

        if args.test and len(args.test) == 1:
            test_name = args.test[0]
            previous_history = runner.load_history()
            result = runner.run_test(test_name, script_driven=args.script_driven)
            success = runner.counts_as_suite_pass(test_name, result)
            prev = runner._format_prev_result(previous_history.get(test_name, []))
            if not result.passed:
                status, _ = runner._test_status(test_name)
                label = "known_broken failure" if status == "known_broken" else "FAILED"
                logging.info(
                    f"Result: {label} ({result.fail_kind}) [{result.duration:.1f}s] "
                    f"— {result.message} ({prev})"
                )
            else:
                logging.info(f"Result: PASSED [{result.duration:.1f}s] ({prev})")
        elif args.test:
            success = runner.run_all(script_driven=args.script_driven, test_names=args.test)
        else:
            success = runner.run_all(script_driven=args.script_driven)

        sys.exit(0 if success else 1)
        
if __name__ == "__main__":
    sys.exit(main())
