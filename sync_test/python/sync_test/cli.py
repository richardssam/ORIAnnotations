import argparse
import sys
import logging

def main():
    parser = argparse.ArgumentParser(description="Automated UI Sync Test Runner")
    parser.add_argument("command", choices=["run"], help="Command to execute")
    parser.add_argument("--config", default="sync_tests.yaml", help="Path to test suite configuration file")
    parser.add_argument("--test", help="Run a specific test by name")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
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
            success = runner.run_test(args.test)
        else:
            success = runner.run_all()
            
        sys.exit(0 if success else 1)
        
if __name__ == "__main__":
    sys.exit(main())
