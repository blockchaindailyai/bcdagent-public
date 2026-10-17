from abc import ABC, abstractmethod

class SourceScraper(ABC):
    @property
    @abstractmethod
    def name(self):
        pass

    @abstractmethod
    def fetch(self):
        pass
