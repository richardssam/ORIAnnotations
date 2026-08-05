import argparse
import sys
import logging

def main():
    parser = argparse.ArgumentParser(description="Automated UI Sync Test Runner")
    parser.add_argument("command", choices=["run"], help="Command to execute")
    parser.add_argument("--config", default="sync_tests.yaml", help="Path to test suite configuration file")
    parser.add_argument("--test", help="Run a specific test by name")
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
    
    if args.command == "run":
        logging.info(f"Starting test runner with config: {args.config}")
        
        from .runner import TestRunner
        try:
            runner = TestRunner(args.config)
        except Exception as e:
            logging.error(f"Configuration error: {e}")
            sys.exit(1)
            
        if args.test:
            previous_history = runner.load_history()
            result = runner.run_test(args.test, script_driven=args.script_driven)
            success = runner.counts_as_suite_pass(args.test, result)
            prev = runner._format_prev_result(previous_history.get(args.test, []))
            if not result.passed:
                status, _ = runner._test_status(args.test)
                label = "known_broken failure" if status == "known_broken" else "FAILED"
                logging.info(
                    f"Result: {label} ({result.fail_kind}) [{result.duration:.1f}s] "
                    f"— {result.message} ({prev})"
                )
            else:
                logging.info(f"Result: PASSED [{result.duration:.1f}s] ({prev})")
        else:
            success = runner.run_all(script_driven=args.script_driven)

        sys.exit(0 if success else 1)
        
if __name__ == "__main__":
    sys.exit(main())
