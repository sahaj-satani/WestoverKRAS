import pandas as pd
import numpy as np
import os
import logging
import matplotlib.pyplot as plt
from scipy import stats
from statsmodels.stats.multitest import fdrcorrection

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Constants
DATA_DIR = r"C:\Users\Sahaj\Documents\UTSW Westover"
OUTPUT_DIR = "crc_output"
P_VALUE_THRESHOLD = 0.05
TOP_PATHWAYS = 10

def load_data(filename):
    filepath = os.path.join(DATA_DIR, filename)
    try:
        df = pd.read_csv(filepath, low_memory=False)
        logger.info(f"Successfully loaded {filename}")
        logger.info(f"Shape of {filename}: {df.shape}")
        logger.info(f"Columns in {filename}: {df.columns.tolist()}")
        return df
    except FileNotFoundError:
        logger.error(f"File not found: {filename}")
        return None
    except pd.errors.EmptyDataError:
        logger.error(f"Empty file: {filename}")
        return None
    except Exception as e:
        logger.error(f"Error loading {filename}: {str(e)}")
        return None

def filter_crc(df):
    if 'OncotreePrimaryDisease' not in df.columns:
        logger.error("OncotreePrimaryDisease column not found in MODEL.CSV")
        return None
    crc_df = df[df['OncotreePrimaryDisease'] == 'Colorectal Adenocarcinoma']
    logger.info(f"Number of Colorectal Adenocarcinoma models: {len(crc_df)}")
    if len(crc_df) == 0:
        logger.warning("No Colorectal Adenocarcinoma models found")
    return crc_df

def filter_kras_mutations(mutations_df, crc_models):
    required_columns = ['ModelID', 'HugoSymbol', 'ProteinChange', 'VepImpact']
    if not all(col in mutations_df.columns for col in required_columns):
        logger.error(f"Required columns not found in OmicsSomaticMutations.CSV. Columns present: {mutations_df.columns.tolist()}")
        return None, None
    
    # Filter for KRAS mutations in Colorectal Adenocarcinoma models
    kras_mutations = mutations_df[
        (mutations_df['HugoSymbol'] == 'KRAS') & 
        (mutations_df['ModelID'].isin(crc_models['ModelID'])) &
        (mutations_df['VepImpact'].isin(['HIGH', 'MODERATE']))  # Consider only high and moderate impact mutations
    ]
    logger.info(f"Number of KRAS mutations in Colorectal Adenocarcinoma models: {len(kras_mutations)}")
    
    # Identify KRAS mutations that occur in at least 3 different cell lines
    kras_mutation_counts = kras_mutations['ProteinChange'].value_counts()
    significant_mutations = kras_mutation_counts[kras_mutation_counts >= 3].index.tolist()
    
    kras_specific_mutations = {mutation: kras_mutations[kras_mutations['ProteinChange'] == mutation] for mutation in significant_mutations}
    
    for mutation, df in kras_specific_mutations.items():
        logger.info(f"Number of KRAS {mutation} mutations: {len(df)}")
    
    return kras_mutations, kras_specific_mutations

def identify_cell_lines(crc_df, kras_mutations, kras_specific_mutations):
    if 'ModelID' not in crc_df.columns:
        logger.error("ModelID column not found in Colorectal Adenocarcinoma dataframe")
        return None, None
    
    specific_cell_lines = {mutation: crc_df[crc_df['ModelID'].isin(df['ModelID'])] for mutation, df in kras_specific_mutations.items()}
    wt_cell_lines = crc_df[~crc_df['ModelID'].isin(kras_mutations['ModelID'])]
    
    for mutation, cell_lines in specific_cell_lines.items():
        logger.info(f"Number of KRAS {mutation} cell lines: {len(cell_lines)}")
    logger.info(f"Number of KRAS WT cell lines: {len(wt_cell_lines)}")
    
    for mutation, cell_lines in specific_cell_lines.items():
        if len(cell_lines) == 0:
            logger.warning(f"No KRAS {mutation} cell lines found")
    if len(wt_cell_lines) == 0:
        logger.warning("No KRAS WT cell lines found")
    
    return specific_cell_lines, wt_cell_lines

def perform_gea(mutations_df, specific_cell_lines, wt_cell_lines):
    results = {}
    for mutation, cell_lines in specific_cell_lines.items():
        if len(cell_lines) == 0 or len(wt_cell_lines) == 0:
            logger.error(f"Cannot perform GEA for {mutation}: One or both groups have no samples")
            results[mutation] = pd.DataFrame()
            continue
        
        all_genes = mutations_df['HugoSymbol'].unique()
        mutation_mutations = mutations_df[mutations_df['ModelID'].isin(cell_lines['ModelID'])]
        wt_mutations = mutations_df[mutations_df['ModelID'].isin(wt_cell_lines['ModelID'])]
        
        mutation_results = []
        for gene in all_genes:
            mutation_count = mutation_mutations[mutation_mutations['HugoSymbol'] == gene]['ModelID'].nunique()
            wt_count = wt_mutations[wt_mutations['HugoSymbol'] == gene]['ModelID'].nunique()
            
            mutation_freq = mutation_count / len(cell_lines)
            wt_freq = wt_count / len(wt_cell_lines)
            
            _, p_value = stats.fisher_exact([[mutation_count, len(cell_lines) - mutation_count],
                                             [wt_count, len(wt_cell_lines) - wt_count]])
            
            mutation_results.append({
                'Gene': gene,
                f'{mutation}_frequency': mutation_freq,
                'WT_frequency': wt_freq,
                'p_value': p_value
            })
        
        results_df = pd.DataFrame(mutation_results)
        _, q_values = fdrcorrection(results_df['p_value'])
        results_df['q_value'] = q_values
        
        significant_results = results_df[results_df['p_value'] < P_VALUE_THRESHOLD].sort_values('p_value')
        logger.info(f"Number of significantly enriched genes for {mutation}: {len(significant_results)}")
        
        results[mutation] = significant_results
    
    return results

def get_top_pathways(gea_results):
    top_pathways = {}
    for mutation, results_df in gea_results.items():
        if results_df.empty:
            logger.warning(f"No enriched pathways found for {mutation}")
            top_pathways[mutation] = {}
            continue
        
        pathway_scores = (results_df[f'{mutation}_frequency'] - results_df['WT_frequency']).abs().sort_values(ascending=False)
        mutation_top_pathways = pathway_scores.head(TOP_PATHWAYS).to_dict()
        
        logger.info(f"Top {len(mutation_top_pathways)} enriched pathways for {mutation}: {', '.join(str(k) for k in mutation_top_pathways.keys())}")
        top_pathways[mutation] = mutation_top_pathways
    
    return top_pathways

def create_pie_chart(data, title, output_path):
    if not data:
        logger.warning("No data available for pie chart")
        return
    
    plt.figure(figsize=(12, 8))
    colors = plt.cm.Set3(np.linspace(0, 1, len(data)))
    
    # Use gene IDs directly without mapping to gene names
    labels = list(data.keys())
    sizes = list(data.values())
    
    wedges, texts, autotexts = plt.pie(sizes, labels=labels, colors=colors, autopct='%1.1f%%', 
                                       startangle=90, pctdistance=0.85, labeldistance=1.05)
    
    plt.title(title, fontsize=16, fontweight='bold')
    
    # Improve label visibility
    for text in texts:
        text.set_fontsize(10)
    for autotext in autotexts:
        autotext.set_fontsize(8)
        autotext.set_fontweight('bold')
    
    plt.axis('equal')
    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    plt.close()
    logger.info(f"Saved pie chart: {output_path}")

def save_results(df, filename):
    if df is None or df.empty:
        logger.warning(f"No data to save for {filename}")
        return
    
    filepath = os.path.join(OUTPUT_DIR, filename)
    df.to_csv(filepath, index=False)
    logger.info(f"Saved results to {filepath}")

def main():
    try:
        # Load data
        models_df = load_data("MODEL.CSV")
        mutations_df = load_data("OmicsSomaticMutations.CSV")
        
        if models_df is None or mutations_df is None:
            logger.error("Failed to load one or more required files. Exiting.")
            return
        
        # Filter for Colorectal Adenocarcinoma
        crc_models = filter_crc(models_df)
        if crc_models is None or crc_models.empty:
            logger.error("Failed to filter Colorectal Adenocarcinoma models. Exiting.")
            return
        
        # Filter for KRAS mutations and specific mutations
        kras_mutations, kras_specific_mutations = filter_kras_mutations(mutations_df, crc_models)
        if kras_mutations is None or kras_specific_mutations is None:
            logger.error("Failed to filter KRAS mutations. Exiting.")
            return
        
        # Identify cell lines
        specific_cell_lines, wt_cell_lines = identify_cell_lines(crc_models, kras_mutations, kras_specific_mutations)
        if specific_cell_lines is None or wt_cell_lines is None:
            logger.error("Failed to identify cell lines. Exiting.")
            return
        
        # Perform Gene Enrichment Analysis
        gea_results = perform_gea(mutations_df, specific_cell_lines, wt_cell_lines)
        
        # Get top pathways
        top_pathways = get_top_pathways(gea_results)
        
        # Create output directory
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        # Save results
        for mutation, cell_lines in specific_cell_lines.items():
            save_results(cell_lines, f"crc_{mutation.replace('.', '_')}_cell_lines.csv")
        save_results(wt_cell_lines, "crc_wt_cell_lines.csv")
        for mutation, results in gea_results.items():
            save_results(results, f"crc_{mutation.replace('.', '_')}_gea_results.csv")
        
        # Create pie charts
        for mutation, pathways in top_pathways.items():
            if pathways:
                create_pie_chart(pathways, f"Top 10 Enriched Pathways in KRAS {mutation} Colorectal Adenocarcinoma Cell Lines Compared to WT", 
                                 os.path.join(OUTPUT_DIR, f"crc_kras_{mutation.replace('.', '_')}_enriched_pathways.png"))
            else:
                logger.warning(f"No enriched pathway data available for pie chart of {mutation}")
        
        logger.info("Analysis completed successfully")
        
    except Exception as e:
        logger.error(f"An unexpected error occurred during the analysis: {str(e)}")
        logger.error("Traceback:", exc_info=True)

if __name__ == "__main__":
    main()
