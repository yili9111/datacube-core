import os
import sys
import threading
import traceback

from _database import Database, CachedResultSet
from _arguments import CommandLineArgs
from _config_file import ConfigFile
import logging

from EOtools.utils import log_multiline

# Set handler for root logger to standard output 
console_handler = logging.StreamHandler(sys.stdout)
#console_handler.setLevel(logging.INFO)
console_handler.setLevel(logging.DEBUG)
console_formatter = logging.Formatter('%(message)s')
console_handler.setFormatter(console_formatter)
logging.root.addHandler(console_handler)

logger = logging.getLogger(__name__)
#logger.setLevel(logging.DEBUG) # Logging level for this module

thread_exception = None

class GDF(object):
    '''
    Class definition for GDF (General Data Framework).
    Manages configuration and database connections.
    '''
    DEFAULT_CONFIG_FILE = 'gdf_default.conf' # N.B: Assumed to reside in code root directory
    
    def get_command_line_params(self):
        command_line_args_object = CommandLineArgs()
        
        return command_line_args_object.arguments
        
    def get_config(self):
        config_dict = {}
        
        # Use default config file if none provided
        config_files_string = self._command_line_params['config_files'] or os.path.join(self._code_root, GDF.DEFAULT_CONFIG_FILE)
        
        # Set list of absolute config file paths from comma-delimited list
        self._config_files = [os.path.abspath(config_file) for config_file in config_files_string.split(',')] 
        log_multiline(logger.debug, self._config_files, 'self._config_files', '\t')
           
        for config_file in self._config_files:
            config_file_object = ConfigFile(config_file)
        
            # Merge all configuration sections from individual config files to config dict
            config_dict.update(config_file_object.configuration)
        
        log_multiline(logger.debug, config_dict, 'config_dict', '\t')
        return config_dict
    
    def get_dbs(self):
        database_dict = {}
        
        # Create a database connection for every valid configuration
        for section_name in sorted(self._configuration.keys()):
            section_dict = self._configuration[section_name]
            try:
                host = section_dict['host']
                port = section_dict['port']
                dbname = section_dict['dbname']
                user = section_dict['user']
                password = section_dict['password']
                
                database = Database(host=host, 
                                    port=port, 
                                    dbname=dbname, 
                                    user=user, 
                                    password=password, 
                                    keep_connection=False, # Assume we don't want connections hanging around
                                    autocommit=True)
                
                database.submit_query('select 1 as test_field') # Test DB connection
                
                database_dict[section_name] = database
            except Exception, e:
                logger.info('Unable to connect to database for %s: %s', section_name, e.message)

        log_multiline(logger.debug, database_dict, 'database_dict', '\t')
        return database_dict
        

    def __init__(self):
        '''Constructor for class GDF
        '''
        self._config_files = [] # List of config files read
        
        self._code_root = os.path.abspath(os.path.dirname(__file__)) # Directory containing module code
        
        # Create master configuration dict containing both command line and config_file parameters
        self._command_line_params = self.get_command_line_params()
        
        if self._command_line_params['debug']:
            logger.setLevel(logging.DEBUG)
        else:
            logger.setLevel(logging.INFO)

                
        # Create master configuration dict containing both command line and config_file parameters
        self._configuration = self.get_config()
                
        # Create master database dict
        self._databases = self.get_dbs()
        
        # Read configuration from databases
        self._db_configuration = self.get_db_config()
        
        log_multiline(logger.debug, self.__dict__, 'GDF.__dict__', '\t')
        
        
    def check_thread_exception(self):
        """"Check for exception raised by previous thread and raise it if found.
        Note that any other threads already underway will be allowed to finish normally.
        """
        global thread_exception
        logger.debug('thread_exception: %s', thread_exception)
        # Check for exception raised by previous thread and raise it if found
        if thread_exception:
            logger.error('Thread error: ' + thread_exception.message)
            raise thread_exception # Raise the exception in the main thread

    def thread_execute(self, db_function, *args, **kwargs):
        """Helper function to capture exception within the thread and set a global
        variable to be checked in the main thread
        N.B: THIS FUNCTION RUNS WITHIN THE SPAWNED THREAD
        """
        global thread_exception
        try:
            db_function(*args, **kwargs)
        except Exception, e:
            thread_exception = e
            log_multiline(logger.error, traceback.format_exc(), 'Error in thread: ' + e.message, '\t')
            raise thread_exception # Re-raise the exception within the thread
        finally:
            logger.debug('Thread finished')

    def get_db_config(self, databases={}):
        '''Function to return a dict with details of all dimensions managed in databases keyed as follows:
        
           <db_name>
               'ndarray_types'
                    <ndarray_type_tag>
                        'measurement_types'
                            <measurement_type_tag>
                        'domains'
                            <domain_name>
                                'dimensions'
                                    <dimension_tag>
                        'dimensions'
                            <dimension_tag>
                   
           This is currently a bit ugly because it retrieves the de-normalised data in a single query and then has to
           build the tree from the flat result set. It could be done in a prettier (but slower) way with multiple queries
        '''
        
        def get_db_data(db_name, databases, result_dict):
            db_dict = {'ndarray_types': {}}
            database = databases[db_name]
            
            ndarray_types = database.submit_query('''-- Query to return all ndarray_type configuration info
select distinct
  ndarray_type_tag,
  ndarray_type_id,
  ndarray_type_name,
  measurement_type_tag,
  measurement_metatype_id,
  measurement_type_id,
  measurement_type_index, 
  measurement_metatype_name,
  measurement_type_name,
  domain_tag,
  domain_id,
  domain_name,
  reference_system.reference_system_id,
  reference_system.reference_system_name,
  reference_system.reference_system_definition,
  reference_system.reference_system_unit,
  dimension_tag,
  dimension_id,
  creation_order,
  dimension_extent,
  dimension_elements,
  dimension_cache,
  dimension_origin,
  index_reference_system.reference_system_id as index_reference_system_id,
  index_reference_system.reference_system_name as index_reference_system_name,
  index_reference_system.reference_system_definition as index_reference_system_definition,
  index_reference_system.reference_system_unit as index_reference_system_unit  
from ndarray_type 
join ndarray_type_measurement_type using(ndarray_type_id)
join measurement_type using(measurement_metatype_id, measurement_type_id)
join measurement_metatype using(measurement_metatype_id)
join ndarray_type_dimension using(ndarray_type_id)
join dimension_domain using(dimension_id, domain_id)
join domain using(domain_id)
join dimension using(dimension_id)
join indexing_type using(indexing_type_id)
join reference_system using (reference_system_id)
left join reference_system index_reference_system on index_reference_system.reference_system_id = ndarray_type_dimension.index_reference_system_id
order by ndarray_type_tag, measurement_type_index, creation_order;
''')
            for record_dict in ndarray_types.record_generator():
                log_multiline(logger.debug, record_dict, 'record_dict', '\t')
                
                ndarray_type_dict = db_dict['ndarray_types'].get(record_dict['ndarray_type_tag'])
                if ndarray_type_dict is None:
                    ndarray_type_dict = {'ndarray_type_tag': record_dict['ndarray_type_tag'],
                                         'ndarray_type_id': record_dict['ndarray_type_id'],
                                         'ndarray_type_name': record_dict['ndarray_type_name'],
                                         'measurement_types': {},
                                         'domains': {},
                                         'dimensions': {}
                                         }

                    db_dict['ndarray_types'][record_dict['ndarray_type_tag']] = ndarray_type_dict
                    
                measurement_type_dict = ndarray_type_dict['measurement_types'].get(record_dict['measurement_type_tag'])
                if measurement_type_dict is None:
                    measurement_type_dict = {'measurement_type_tag': record_dict['measurement_type_tag'],
                                             'measurement_metatype_id': record_dict['measurement_metatype_id'],
                                             'measurement_type_id': record_dict['measurement_type_id'],
                                             'measurement_type_index': record_dict['measurement_type_index'],
                                             'measurement_metatype_name': record_dict['measurement_metatype_name'],
                                             'measurement_type_name': record_dict['measurement_type_name']
                                             }

                    ndarray_type_dict['measurement_types'][record_dict['measurement_type_tag']] = measurement_type_dict
                    
                domain_dict = ndarray_type_dict['domains'].get(record_dict['domain_tag'])
                if domain_dict is None:
                    domain_dict = {'domain_tag': record_dict['domain_tag'],
                                   'domain_id': record_dict['domain_id'],
                                   'domain_name': record_dict['domain_name'],
                                   'reference_system_id': record_dict['reference_system_id'],
                                   'reference_system_name': record_dict['reference_system_name'],
                                   'reference_system_definition': record_dict['reference_system_definition'],
                                   'reference_system_unit': record_dict['reference_system_unit'], 
                                   'dimensions': {}
                                   }

                    ndarray_type_dict['domains'][record_dict['domain_tag']] = domain_dict
                    
                dimension_dict = domain_dict['dimensions'].get(record_dict['dimension_tag'])
                if dimension_dict is None:
                    dimension_dict = {'dimension_tag': record_dict['dimension_tag'],
                                      'dimension_id': record_dict['dimension_id'],
                                      'creation_order': record_dict['creation_order'],
                                      'dimension_extent': record_dict['dimension_extent'],
                                      'dimension_elements': record_dict['dimension_elements'],
                                      'dimension_cache': record_dict['dimension_cache'],
                                      'dimension_origin': record_dict['dimension_origin'],
                                      'index_reference_system_id': record_dict['index_reference_system_id'],
                                      'index_reference_system_name': record_dict['index_reference_system_name'],
                                      'index_reference_system_definition': record_dict['index_reference_system_definition'],
                                      'index_reference_system_unit': record_dict['index_reference_system_unit']
                                      }

                    # Store a reference both under domains and ndarray_type
                    domain_dict['dimensions'][record_dict['dimension_tag']] = dimension_dict
                    ndarray_type_dict['dimensions'][record_dict['dimension_tag']] = dimension_dict
                    
                    
#            log_multiline(logger.info, db_dict, 'db_dict', '\t')
            result_dict[db_name] = db_dict
            # End of per-DB function
        
        databases = databases or self._databases
        
        result_dict = {} # Nested dict containing ndarray_type details for all databases

        thread_list = []
        for db_name in sorted(databases.keys()):
#            check_thread_exception()
            process_thread = threading.Thread(target=self.thread_execute,                    
                                              args=(get_db_data, 
                                                    db_name, 
                                                    databases, 
                                                    result_dict
                                                    )
                                              )
            thread_list.append(process_thread)
            process_thread.setDaemon(False)
            process_thread.start()
            logger.debug('Started thread for get_db_data(%s, %s, %s)', db_name, databases, result_dict)

        # Wait for all threads to finish
        for process_thread in thread_list:
            self.check_thread_exception()
            process_thread.join()

        self.check_thread_exception()
        logger.debug('All threads finished')

        log_multiline(logger.debug, result_dict, 'result_dict', '\t')
        return result_dict
            
    
    def get_ndarrays(self, dimension_range_dict, ndarray_type_tags=[], databases={}): 
        '''
        Function to return all ndarrays which fall in the specified dimensional ranges
        
        Parameter:
            dimension_range_dict: dict defined as {<dimension_tag>: (<min_value>, <max_value>), 
                                                   <dimension_tag>: (<min_value>, <max_value>)...}
        '''
        def get_db_ndarrays(db_name, dimension_range_dict, result_dict, ndarray_type_tags, databases):
            db_ndarray_dict = {}
            database = databases[db_name]
            db_config_dict = self._db_configuration[db_name]
            
            for ndarray_type in db_config_dict['ndarray_types'].values():
                
                ndarray_type_tag = ndarray_type['ndarray_type_tag']
                logger.debug('ndarray_type_tag = %s', ndarray_type_tag)
                
                # Skip any ndarray_types if they are not in a specified list
                if ndarray_type_tags and (ndarray_type_tag not in ndarray_type_tags):
                    continue
                
                # list of dimension_tags for ndarray_type sorted by creation order
                ndarray_type_dimension_tags = [dimension['dimension_tag'] for dimension in sorted(ndarray_type['dimensions'].values(), key=lambda dimension: dimension['creation_order'])]
                logger.debug('ndarray_type_dimension_tags = %s', ndarray_type_dimension_tags)
                # list of dimension_tags for range query sorted by creation order
                range_dimension_tags = [dimension_tag for dimension_tag in ndarray_type_dimension_tags if dimension_tag in dimension_range_dict.keys()]
                logger.debug('range_dimension_tags = %s', range_dimension_tags)
                
                # Create a dict of ndarrays keyed by indices for each ndarray_type
                ndarray_dict = {}
                
                SQL = '''-- Find ndarrays which fall in range
select distinct'''
                for dimension_tag in ndarray_type_dimension_tags:
                    SQL +='''
%s.ndarray_dimension_index as %s_index,
%s.ndarray_dimension_min as %s_min,
%s.ndarray_dimension_max as %s_max,'''.replace('%s', dimension_tag)
                SQL +='''
ndarray.*
from ndarray
'''                    
                for dimension_tag in ndarray_type_dimension_tags:
                    SQL += '''join (
select *
from dimension 
    join dimension_domain using(dimension_id)
    join ndarray_dimension using(dimension_id, domain_id)
    where ndarray_type_id = %d
    and ndarray_version = 0
    and dimension.dimension_tag = '%s'
''' % (ndarray_type['ndarray_type_id'], 
       dimension_tag
       )
                    # Apply range filters
                    if dimension_tag in range_dimension_tags:
#===============================================================================
#                         SQL += '''and (ndarray_dimension_min between %f and %f 
#         or ndarray_dimension_max between %f and %f)
# ''' % (dimension_range_dict[dimension_tag][0],
#        dimension_range_dict[dimension_tag][1],
#        dimension_range_dict[dimension_tag][0],
#        dimension_range_dict[dimension_tag][1]
#        )
#===============================================================================
                        SQL += '''and (ndarray_dimension_min < %f 
        and ndarray_dimension_max > %f)
''' % (dimension_range_dict[dimension_tag][1],
       dimension_range_dict[dimension_tag][0]
       )

                    SQL += ''') %s using(ndarray_type_id, ndarray_id, ndarray_version)
''' % (dimension_tag)

                SQL +='''
order by ''' + '_index, '.join(ndarray_type_dimension_tags) + '''_index;
'''
            
                log_multiline(logger.debug, SQL , 'SQL', '\t')
    
                ndarrays = database.submit_query(SQL)
                
                for record_dict in ndarrays.record_generator():
                    log_multiline(logger.debug, record_dict, 'record_dict', '\t')
                    indices = tuple([record_dict[dimension_tag.lower() + '_index'] for dimension_tag in ndarray_type_dimension_tags])
    
                    ndarray_dict[indices] = record_dict
                    
                if ndarray_dict:
                    db_ndarray_dict[ndarray_type_tag] = ndarray_dict
                                
#            log_multiline(logger.info, db_dict, 'db_dict', '\t')
            result_dict[db_name] = db_ndarray_dict
        
        databases = databases or self._databases
        
        result_dict = {} # Nested dict containing ndarray_type details for all databases

        thread_list = []
        for db_name in sorted(databases.keys()):
            logger.debug('db_name = %s', db_name)
#            check_thread_exception()
            process_thread = threading.Thread(target=self.thread_execute,
                                              args=(get_db_ndarrays, 
                                                    db_name, 
                                                    dimension_range_dict, 
                                                    result_dict, 
                                                    ndarray_type_tags, 
                                                    databases
                                                    )
                    )
            thread_list.append(process_thread)
            process_thread.setDaemon(False)
            process_thread.start()
            logger.debug('Started thread for get_db_ndarrays(%s, %s, %s, %s, %s)', db_name, dimension_range_dict, result_dict, ndarray_type_tags, databases)

        # Wait for all threads to finish
        for process_thread in thread_list:
            self.check_thread_exception()
            process_thread.join()

        self.check_thread_exception()
        logger.debug('All threads finished')

        log_multiline(logger.debug, result_dict, 'result_dict', '\t')
        return result_dict

    
    
    # Define properties for GDF class
    @property
    def code_root(self):
        return self._code_root
    
    @property
    def config_files(self):
        return self._config_files
    
    @property
    def command_line_params(self):
        return self._command_line_params
    
    @property
    def configuration(self):
        return self._configuration
    
    @property
    def databases(self):
        return self._databases

    @property
    def db_configuration(self):
        return self._db_configuration
    
        
