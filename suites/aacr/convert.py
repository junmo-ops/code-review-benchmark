"""Run the inherited AACR converter in its own module namespace/process."""
from .upstream import activate
activate()
from converters.aacr_bench import main
if __name__ == "__main__":main()
